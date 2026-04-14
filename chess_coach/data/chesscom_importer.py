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
    collect_opponents: bool = True,
) -> int:
    """Fetch the last ``months_back`` months of games for ``chess_com_username``
    and store them as ``user_games`` rows for ``user_id``. Returns count of
    newly inserted games (duplicates are ignored).

    When ``collect_opponents`` is true, we also queue up the unique
    opponents we encountered to have their own public game history
    imported in the background (so the Practice screen can offer them as
    pre-trained dropdown choices).
    """
    import time
    t0 = time.monotonic()
    client = ChessComClient()
    inserted = 0
    try:
        archives = await client.archives(chess_com_username)
        archives = archives[-months_back:]
        print(
            f"[chesscom] {chess_com_username}: {len(archives)} monthly archives "
            f"(ETA ~{max(1, len(archives)*5)}s)"
        )
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

        print(
            f"[chesscom] {chess_com_username}: +{inserted} games "
            f"in {time.monotonic()-t0:.1f}s"
        )

        if collect_opponents:
            # Find opponents we just imported but haven't trained on yet, and
            # kick off a best-effort opponent_games import for the top ones.
            opp_rows = await db.fetch(
                """
                SELECT ug.opponent_name, COUNT(*) AS games_vs
                FROM user_games ug
                LEFT JOIN opponent_games og ON og.opponent = ug.opponent_name
                WHERE ug.user_id = $1
                  AND ug.opponent_name IS NOT NULL
                  AND ug.source = 'chess_com'
                GROUP BY ug.opponent_name
                HAVING COUNT(DISTINCT og.id) = 0
                ORDER BY COUNT(*) DESC
                LIMIT 10
                """,
                user_id,
            )
            if opp_rows:
                print(
                    f"[chesscom] auto-collecting opponent games for "
                    f"{len(opp_rows)} new opponents (ETA ~{len(opp_rows)*10}s)"
                )
            for r in opp_rows:
                opp = r["opponent_name"]
                try:
                    await import_opponent_games(opp, months_back=6)
                except Exception:
                    continue
        return inserted
    finally:
        await client.aclose()


async def recommended_opponents(user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return opponents for the practice dropdown, ranked by how much
    the user *needs* to practice against them.

    Score mix (higher = worse = better to train on):
      * losses vs this opponent          (weight 1.0)
      * draws vs this opponent           (weight 0.2)
      * games played vs this opponent    (weight 0.05, diminishing)
      * average eval-swing in games      (weight 0.005 per cp, if available)

    We also surface whether we already have fine-tuned weights for the
    opponent so the UI can render a badge.
    """
    rows = await db.fetch(
        """
        WITH per_opp AS (
            SELECT
                ug.opponent_name                                   AS opponent,
                COUNT(*)                                           AS games,
                SUM(CASE WHEN ug.result = 'loss' THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN ug.result = 'draw' THEN 1 ELSE 0 END) AS draws,
                SUM(CASE WHEN ug.result = 'win'  THEN 1 ELSE 0 END) AS wins,
                MAX(ug.opponent_rating)                            AS peak_rating,
                MAX(ug.played_at)                                  AS last_played
            FROM user_games ug
            WHERE ug.user_id = $1
              AND ug.opponent_name IS NOT NULL
            GROUP BY ug.opponent_name
        )
        SELECT p.opponent,
               p.games, p.wins, p.draws, p.losses,
               p.peak_rating,
               p.last_played,
               (SELECT COUNT(*) FROM opponent_games og WHERE og.opponent = p.opponent) AS training_games,
               (SELECT weights_path FROM maia_models m
                 WHERE m.opponent = p.opponent
                 ORDER BY trained_at DESC LIMIT 1)                                   AS weights_path
        FROM per_opp p
        ORDER BY
            (p.losses * 1.0 + p.draws * 0.2 + LEAST(p.games, 50) * 0.05) DESC,
            p.games DESC
        LIMIT $2
        """,
        user_id,
        limit,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        games = r["games"] or 0
        losses = r["losses"] or 0
        draws = r["draws"] or 0
        wins = r["wins"] or 0
        score = losses * 1.0 + draws * 0.2 + min(games, 50) * 0.05
        out.append({
            "opponent": r["opponent"],
            "games": games,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "win_rate": round(wins / games, 3) if games else 0.0,
            "peak_rating": r["peak_rating"],
            "last_played": r["last_played"].isoformat() if r["last_played"] else None,
            "training_games": int(r["training_games"] or 0),
            "has_trained_model": bool(r["weights_path"]),
            "score": round(score, 2),
        })
    return out


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
    import time
    t0 = time.monotonic()
    client = ChessComClient()
    inserted = 0
    try:
        archives = await client.archives(opponent)
        archives = archives[-months_back:]
        print(
            f"[chesscom:opp] {opponent}: {len(archives)} archives "
            f"(ETA ~{max(1, len(archives)*5)}s)"
        )
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
        print(
            f"[chesscom:opp] {opponent}: +{inserted} games "
            f"in {time.monotonic()-t0:.1f}s"
        )
        return inserted
    finally:
        await client.aclose()
