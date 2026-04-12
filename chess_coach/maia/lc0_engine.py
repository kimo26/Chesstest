"""UCI wrapper around lc0 configured for maia-individual weights.

Maia models are lc0 policy networks trained without tree search — they're
intended to play at ``nodes=1`` so the move probability distribution of
the network is the move distribution of the engine. We expose:

* ``LC0Engine`` — a single lc0 process loaded with one weights file.
* ``get_engine_for_opponent(opponent)`` — pick the fine-tuned weights for an
  opponent from the ``maia_models`` table and return a cached engine handle.
* ``select_move`` — play a move; samples from the policy rather than always
  taking the argmax, which is what makes Maia play like a human.

The engine processes are managed by an async-safe registry keyed by weights
path so a single process is shared across requests.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine

from .. import db
from ..config import settings


@dataclass
class EngineHandle:
    engine: chess.engine.SimpleEngine
    weights_path: Path
    lock: asyncio.Lock


class LC0Engine:
    """Async-friendly wrapper for a single lc0 process."""

    def __init__(self, weights_path: Path, *, backend: str = "cuda-fp16") -> None:
        self.weights_path = Path(weights_path)
        if not self.weights_path.exists():
            raise FileNotFoundError(f"Maia weights not found: {self.weights_path}")
        self._handle: EngineHandle | None = None
        self._backend = backend

    async def start(self) -> None:
        if self._handle is not None:
            return
        loop = asyncio.get_running_loop()
        engine = await loop.run_in_executor(
            None,
            lambda: chess.engine.SimpleEngine.popen_uci(
                [
                    settings.lc0_path,
                    f"--weights={self.weights_path}",
                    f"--backend={self._backend}",
                    "--temperature=1.0",      # sample from policy
                    "--temp-visit-offset=0",
                    "--temp-value-cutoff=100",
                    "--no-smart-pruning",
                ],
            ),
        )
        # maia is a policy-only network — no tree search.
        engine.configure({"Threads": settings.lc0_threads})
        self._handle = EngineHandle(engine=engine, weights_path=self.weights_path, lock=asyncio.Lock())

    async def close(self) -> None:
        if self._handle is None:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._handle.engine.quit)
        except chess.engine.EngineTerminatedError:
            pass
        self._handle = None

    async def play(self, board: chess.Board, *, nodes: int | None = None) -> chess.Move:
        if self._handle is None:
            await self.start()
        assert self._handle is not None

        nodes = nodes or settings.lc0_nodes
        async with self._handle.lock:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._handle.engine.play(board, chess.engine.Limit(nodes=nodes)),
            )
        if result.move is None:
            raise RuntimeError("lc0 returned no move")
        return result.move

    async def policy(
        self,
        board: chess.Board,
        *,
        nodes: int | None = None,
    ) -> dict[str, float]:
        """Return the policy distribution over legal moves (UCI -> prob).

        We ask lc0 to analyse the position with multipv=all legal moves and
        convert each child's visit share / wdl into a probability. This is
        what lets us apply repertoire biasing before sampling.
        """
        if self._handle is None:
            await self.start()
        assert self._handle is not None

        legal = list(board.legal_moves)
        async with self._handle.lock:
            loop = asyncio.get_running_loop()
            infos = await loop.run_in_executor(
                None,
                lambda: self._handle.engine.analyse(
                    board,
                    chess.engine.Limit(nodes=nodes or settings.lc0_nodes),
                    multipv=len(legal),
                ),
            )

        probs: dict[str, float] = {}
        total = 0.0
        for info in infos:
            pv = info.get("pv") or []
            if not pv:
                continue
            m = pv[0].uci()
            # lc0 reports WDL; use the win probability as a proxy.
            wdl = info.get("wdl")
            if wdl is not None:
                w = wdl.pov(board.turn).expectation()
                probs[m] = float(w)
                total += float(w)
            else:
                score = info.get("score")
                if score is None:
                    continue
                cp = score.pov(board.turn).score(mate_score=10_000) or 0
                probs[m] = float(max(1, 1000 + cp))
                total += probs[m]

        if total <= 0:
            # Fall back to uniform over legal moves.
            return {m.uci(): 1.0 / len(legal) for m in legal}

        return {m: v / total for m, v in probs.items()}

    async def sample_move(
        self,
        board: chess.Board,
        *,
        repertoire_bias: dict[str, float] | None = None,
        temperature: float = 1.0,
    ) -> chess.Move:
        """Sample a move from Maia's policy, optionally re-weighted by the
        opponent's historical repertoire."""
        probs = await self.policy(board)

        if repertoire_bias:
            boosted: dict[str, float] = {}
            for mv, p in probs.items():
                bias = repertoire_bias.get(mv, 0.0)
                # Multiplicative boost of up to 3x for heavily-played moves.
                boosted[mv] = p * (1.0 + 2.0 * bias)
            total = sum(boosted.values())
            if total > 0:
                probs = {m: v / total for m, v in boosted.items()}

        if temperature != 1.0:
            # Apply temperature softening / sharpening.
            import math
            adjusted = {m: p ** (1.0 / max(1e-3, temperature)) for m, p in probs.items()}
            total = sum(adjusted.values())
            probs = {m: v / total for m, v in adjusted.items()}

        moves = list(probs.keys())
        weights = [probs[m] for m in moves]
        chosen = random.choices(moves, weights=weights, k=1)[0]
        return chess.Move.from_uci(chosen)


# ----------------------------------------------------------------------------
# Per-opponent engine registry
# ----------------------------------------------------------------------------
_registry: dict[str, LC0Engine] = {}
_registry_lock = asyncio.Lock()


async def get_engine_for_opponent(opponent: str) -> LC0Engine:
    async with _registry_lock:
        if opponent in _registry:
            return _registry[opponent]

        row = await db.fetchrow(
            """
            SELECT weights_path FROM maia_models
            WHERE opponent = $1
            ORDER BY trained_at DESC
            LIMIT 1
            """,
            opponent,
        )
        if row is None:
            # Fall back to the base Maia weights.
            weights_path = Path(settings.maia_base_weights)
        else:
            weights_path = Path(row["weights_path"])

        engine = LC0Engine(weights_path)
        await engine.start()
        _registry[opponent] = engine
        return engine


async def shutdown_all_engines() -> None:
    async with _registry_lock:
        for eng in _registry.values():
            await eng.close()
        _registry.clear()
