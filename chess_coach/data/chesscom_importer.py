"""Chess.com public data API importer.

Chess.com exposes monthly game archives at
``/pub/player/{user}/games/{YYYY}/{MM}``. We walk the archive URL list,
fetch each month serially (parallel requests trigger 429), parse every PGN
with python-chess, and insert a row into ``user_games`` (for the logged-in
user) or ``opponent_games`` (for opponents we're fine-tuning Maia on).

Both importers are idempotent thanks to the ``(source, external_id)``
uniqueness constraints in the schema.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
from typing import Any, Iterable

import chess
import chess.pgn
import httpx

from .. import db
from ..config import settings


class ChessComClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": settings.user_agent},
            base_url=settings.chess_com_api_url,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> dict[str, Any]:
        backoff = 2.0
        for _ in range(5):
            resp = await self._client.get(path)
            if resp.status_code == 429:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"chess.com: too many 429s on {path}")

    async def archives(self, username: str) -> list[str]:
        data = await self._get(f"/player/{username.lower()}/games/archives")
        return data.get("archives", [])

    async def month_games(self, archive_url: str) -> list[dict[str, Any]]:
        path = archive_url.replace(settings.chess_com_api_url, "")
        data = await self._get(path)
        return data.get("games", [])


# ----------------------------------------------------------------------------
# Game parsing helpers
# ----------------------------------------------------------------------------

def _parse_game(pgn_text: str) -> chess.pgn.Game | None:
    return chess.pgn.read_game(io.StringIO(pgn_text))


def _user_color(game_json: dict[str, Any], username: str) -> str | None:
    w = (game_json.get("white") or {}).get("username", "").lower()
    b = (game_json.get("black") or {}).get("username", "").lower()
    u = username.lower()
    if w == u:
        return "white"
    if b == u:
        return "black"
    return None


def _result_label(game_json: dict[str, Any], color: str) -> tuple[str, str | None]:
    side = game_json.get(color) or {}
    res = side.get("result", "")
    win_map = {"win"}
    draw_map = {
        "agreed", "repetition", "stalemate", "insufficient",
        "50move", "timevsinsufficient",
    }
    if res in win_map:
        return "win", res
    if res in draw_map:
        return "draw", res
    return "loss", res


def _final_fen(game: chess.pgn.Game) -> str:
    board = game.board()
    for mv in game.mainline_moves():
        board.push(mv)
    return board.fen()


async def _match_opening_node(game: chess.pgn.Game) -> tuple[int | None, str | None, str | None]:
    """Walk the game's moves and pick the deepest opening_nodes row we find.
    Returns (node_id, eco, name) or (None, None, None)."""
    board = chess.Board()
    node_id: int | None = None
    eco: str | None = None
    name: str | None = None

    for mv in game.mainline_moves():
        board.push(mv)
        canon = " ".join(board.fen().split()[:4])
        row = await db.fetchrow(
            "SELECT id, eco_code, opening_name FROM opening_nodes WHERE fen_canonical = $1",
            canon,
        )
        if row is None:
            continue
        node_id = int(row["id"])
        eco = row["eco_code"]
        name = row["opening_name"]

    return node_id, eco, name


# ----------------------------------------------------------------------------
# User game import
# ----------------------------------------------------------------------------

async def import_user_games(
    user_id: int,
    chess_com_username: str,
    *,
    months_back: int = 12,
) -> int:
    """Fetch the last ``months_back`` months of games for ``chess_com_username``
    and store them as ``user_games`` rows for ``user_id``. Returns count of
    newly inserted games (duplicates are ignored)."""
    client = ChessComClient()
    inserted = 0
    try:
        archives = await client.archives(chess_com_username)
        archives = archives[-months_back:]
        for archive_url in archives:
            games = await client.month_games(archive_url)
            for g in games:
                pgn = g.get("pgn")
                if not pgn:
                    continue
                parsed = _parse_game(pgn)
                if parsed is None:
                    continue
                color = _user_color(g, chess_com_username)
                if color is None:
                    continue
                result, result_detail = _result_label(g, color)

                eco = parsed.headers.get("ECO") or None
                opening_name = parsed.headers.get("Opening") or None
                ts = g.get("end_time") or 0
                played_at = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)

                opening_node_id, eco_matched, name_matched = await _match_opening_node(parsed)
                eco = eco_matched or eco
                opening_name = name_matched or opening_name

                user_info = g.get(color) or {}
                opponent_info = g.get("black" if color == "white" else "white") or {}

                move_count = sum(1 for _ in parsed.mainline_moves())
                fen_final = _final_fen(parsed)

                try:
                    await db.execute(
                        """
                        INSERT INTO user_games (
                            user_id, source, external_id, pgn, fen_final,
                            eco_code, opening_name, opening_node_id,
                            user_color, user_rating, opponent_name, opponent_rating,
                            result, result_detail, accuracy,
                            time_control, time_class, played_at, move_count
                        ) VALUES (
                            $1,'chess_com',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,
                            $12,$13,$14,$15,$16,$17,$18
                        )
                        ON CONFLICT (user_id, source, external_id) DO NOTHING
                        """,
                        user_id,
                        str(g.get("uuid") or g.get("url") or ""),
                        pgn,
                        fen_final,
                        eco,
                        opening_name,
                        opening_node_id,
                        color,
                        int(user_info.get("rating") or 0) or None,
                        opponent_info.get("username"),
                        int(opponent_info.get("rating") or 0) or None,
                        result,
                        result_detail,
                        (g.get("accuracies") or {}).get(color),
                        str(g.get("time_control") or ""),
                        g.get("time_class"),
                        played_at,
                        move_count,
                    )
                    inserted += 1
                except Exception:
                    # keep going on bad rows
                    continue
        return inserted
    finally:
        await client.aclose()


# ----------------------------------------------------------------------------
# Opponent game import (for maia-individual fine-tuning)
# ----------------------------------------------------------------------------

async def import_opponent_games(
    opponent: str,
    *,
    months_back: int = 24,
    min_moves: int = 10,
) -> int:
    """Pull the last ``months_back`` months of ``opponent``'s games from
    chess.com and store them into ``opponent_games``. Used as training
    material for a maia-individual model."""
    client = ChessComClient()
    inserted = 0
    try:
        archives = await client.archives(opponent)
        archives = archives[-months_back:]
        for archive_url in archives:
            games = await client.month_games(archive_url)
            for g in games:
                pgn = g.get("pgn")
                if not pgn:
                    continue
                parsed = _parse_game(pgn)
                if parsed is None:
                    continue

                color = _user_color(g, opponent)
                if color is None:
                    continue

                move_count = sum(1 for _ in parsed.mainline_moves())
                if move_count < min_moves:
                    continue

                player_info = g.get(color) or {}
                ts = g.get("end_time") or 0
                played_at = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)

                try:
                    await db.execute(
                        """
                        INSERT INTO opponent_games (
                            opponent, source, external_id, pgn,
                            opponent_color, opponent_rating, time_class, played_at
                        ) VALUES ($1,'chess_com',$2,$3,$4,$5,$6,$7)
                        ON CONFLICT (opponent, source, external_id) DO NOTHING
                        """,
                        opponent,
                        str(g.get("uuid") or g.get("url") or ""),
                        pgn,
                        color,
                        int(player_info.get("rating") or 0) or None,
                        g.get("time_class"),
                        played_at,
                    )
                    inserted += 1
                except Exception:
                    continue
        return inserted
    finally:
        await client.aclose()
