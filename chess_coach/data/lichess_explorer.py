"""Crawler for the Lichess opening explorer.

The explorer streams JSON per position. We traverse the opening tree BFS,
appending moves to the ``play`` parameter. For every position we:

1. Store / update an ``opening_nodes`` row with the current FEN + aggregates.
2. Enqueue every child move whose total game count is above a threshold.

The explorer documents 429 as "wait 60 seconds". We respect that with a
backoff loop; the steady-state rate is ~2 req/sec.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

import chess
import httpx

from .. import db
from ..config import settings


@dataclass
class ExplorerMove:
    uci: str
    san: str
    white: int
    draws: int
    black: int
    average_rating: int
    opening_eco: str | None
    opening_name: str | None

    @property
    def total(self) -> int:
        return self.white + self.draws + self.black


@dataclass
class ExplorerPosition:
    white: int
    draws: int
    black: int
    moves: list[ExplorerMove] = field(default_factory=list)
    opening_eco: str | None = None
    opening_name: str | None = None


class LichessExplorer:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        variant: str = "lichess",
        speeds: tuple[str, ...] = ("blitz", "rapid", "classical"),
        ratings: tuple[int, ...] = (1600, 1800, 2000, 2200, 2500),
    ) -> None:
        self.base_url = (base_url or settings.lichess_explorer_url).rstrip("/")
        self.variant = variant
        self.speeds = speeds
        self.ratings = ratings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": settings.user_agent},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, play_uci: list[str]) -> ExplorerPosition:
        params: dict[str, Any] = {
            "play": ",".join(play_uci),
            "speeds": ",".join(self.speeds),
            "ratings": ",".join(str(r) for r in self.ratings),
            "moves": 24,
        }
        url = f"{self.base_url}/{self.variant}"

        backoff = 2.0
        for _ in range(6):
            resp = await self._client.get(url, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(60)
                continue
            if resp.status_code >= 500:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        else:
            raise RuntimeError("Lichess explorer repeatedly failed")

        opening = data.get("opening") or {}
        moves = [
            ExplorerMove(
                uci=m["uci"],
                san=m["san"],
                white=m.get("white", 0),
                draws=m.get("draws", 0),
                black=m.get("black", 0),
                average_rating=m.get("averageRating", 0),
                opening_eco=(m.get("opening") or {}).get("eco"),
                opening_name=(m.get("opening") or {}).get("name"),
            )
            for m in data.get("moves", [])
        ]
        return ExplorerPosition(
            white=data.get("white", 0),
            draws=data.get("draws", 0),
            black=data.get("black", 0),
            moves=moves,
            opening_eco=opening.get("eco"),
            opening_name=opening.get("name"),
        )


async def _upsert_node(
    play_uci: list[str],
    pos: ExplorerPosition,
) -> int:
    """Insert or update an opening_nodes row for the position reached by
    ``play_uci`` and return its id."""
    board = chess.Board()
    for u in play_uci:
        board.push_uci(u)
    fen = board.fen()

    # Rebuild SAN move sequence: "1.e4 c5 2.Nf3 ..."
    san_board = chess.Board()
    parts: list[str] = []
    for i, umove in enumerate(play_uci):
        mv = chess.Move.from_uci(umove)
        san = san_board.san(mv)
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}.{san}")
        else:
            parts.append(san)
        san_board.push(mv)
    move_sequence = " ".join(parts) if parts else ""

    avg_rating = 0
    total = pos.white + pos.draws + pos.black
    if pos.moves and total:
        avg_rating = int(
            sum(m.average_rating * m.total for m in pos.moves)
            / max(1, sum(m.total for m in pos.moves))
        )

    row = await db.fetchrow(
        """
        INSERT INTO opening_nodes (
            eco_code, opening_name, move_uci, move_san,
            move_sequence, fen, depth,
            white_wins, draws, black_wins, avg_rating
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        ON CONFLICT (fen_canonical) DO UPDATE SET
            eco_code     = COALESCE(opening_nodes.eco_code, EXCLUDED.eco_code),
            opening_name = COALESCE(opening_nodes.opening_name, EXCLUDED.opening_name),
            move_sequence= EXCLUDED.move_sequence,
            white_wins   = EXCLUDED.white_wins,
            draws        = EXCLUDED.draws,
            black_wins   = EXCLUDED.black_wins,
            avg_rating   = EXCLUDED.avg_rating,
            updated_at   = NOW()
        RETURNING id
        """,
        pos.opening_eco,
        pos.opening_name,
        play_uci[-1] if play_uci else None,
        parts[-1].split(".")[-1] if parts else None,
        move_sequence,
        fen,
        len(play_uci),
        pos.white,
        pos.draws,
        pos.black,
        avg_rating or None,
    )
    return int(row["id"])


async def crawl_tree(
    *,
    max_depth: int = 12,
    min_games: int = 1000,
    max_children_per_node: int = 8,
    request_delay_s: float = 0.5,
) -> int:
    """BFS-crawl the Lichess explorer.

    Expands a move only when it has at least ``min_games`` games and we are
    still within ``max_depth``. Returns the number of nodes persisted.
    """
    explorer = LichessExplorer()
    try:
        queue: deque[list[str]] = deque([[]])
        seen: set[str] = set()
        n_nodes = 0

        while queue:
            play = queue.popleft()
            board = chess.Board()
            for u in play:
                board.push_uci(u)
            canon = " ".join(board.fen().split()[:4])
            if canon in seen:
                continue
            seen.add(canon)

            pos = await explorer.fetch(play)
            await _upsert_node(play, pos)
            n_nodes += 1

            if len(play) >= max_depth:
                continue

            top_moves = sorted(pos.moves, key=lambda m: m.total, reverse=True)[
                :max_children_per_node
            ]
            for mv in top_moves:
                if mv.total < min_games:
                    continue
                queue.append(play + [mv.uci])

            await asyncio.sleep(request_delay_s)

        return n_nodes
    finally:
        await explorer.aclose()
