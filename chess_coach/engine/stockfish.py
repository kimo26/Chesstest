"""Stockfish engine pool.

python-chess launches Stockfish as a subprocess; we keep a small pool of
workers so the puzzle extractor can parallelise across games. Each worker
exposes an ``analyse(board, limit, multipv)`` coroutine — the pool picks
the first free worker on demand.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import chess
import chess.engine

from ..config import settings


def score_to_cp(pov_score: chess.engine.PovScore, mate_cp: int = 10000) -> int:
    """Convert a PovScore to an integer centipawn value. Mate is clamped to
    ±``mate_cp`` so arithmetic doesn't overflow."""
    s = pov_score.score(mate_score=mate_cp)
    return s if s is not None else 0


class _Worker:
    def __init__(self, engine: chess.engine.SimpleEngine) -> None:
        self.engine = engine
        self.lock = asyncio.Lock()

    def close(self) -> None:
        try:
            self.engine.quit()
        except chess.engine.EngineTerminatedError:
            pass


class StockfishPool:
    def __init__(self, size: int = 2) -> None:
        self.size = size
        self._workers: list[_Worker] = []
        self._init_lock = asyncio.Lock()

    async def _ensure_workers(self) -> None:
        async with self._init_lock:
            if self._workers:
                return
            loop = asyncio.get_running_loop()
            for _ in range(self.size):
                eng = await loop.run_in_executor(
                    None,
                    lambda: chess.engine.SimpleEngine.popen_uci(settings.stockfish_path),
                )
                eng.configure(
                    {
                        "Threads": settings.stockfish_threads,
                        "Hash": settings.stockfish_hash_mb,
                    }
                )
                self._workers.append(_Worker(eng))

    async def close(self) -> None:
        for w in self._workers:
            w.close()
        self._workers.clear()

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[_Worker]:
        await self._ensure_workers()
        while True:
            for w in self._workers:
                if not w.lock.locked():
                    async with w.lock:
                        yield w
                        return
            await asyncio.sleep(0.01)

    async def analyse(
        self,
        board: chess.Board,
        *,
        nodes: int | None = None,
        depth: int | None = None,
        multipv: int = 1,
    ) -> list[chess.engine.InfoDict]:
        await self._ensure_workers()
        limit = chess.engine.Limit(nodes=nodes, depth=depth)
        async with self._acquire() as worker:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(
                None,
                lambda: worker.engine.analyse(board, limit, multipv=multipv),
            )
            if multipv == 1 and isinstance(info, dict):
                return [info]
            return info  # type: ignore[return-value]
