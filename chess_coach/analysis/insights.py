"""Retroactive game analysis and aggregated insights.

Analyses stored ``user_games`` with Stockfish, caches results in
``game_analyses``, and provides aggregate statistics for the Insights page.
"""
from __future__ import annotations

import io
import json
from typing import Any

import chess
import chess.pgn

from .. import db
from ..config import settings
from ..engine.stockfish import StockfishPool, score_to_cp
from ..llm import get_client
from ..llm.prompts import PROMPTS


# ── Per-game analysis ────────────────────────────────────────────────────

async def analyse_game(game_id: int, pool: StockfishPool | None = None) -> dict[str, Any]:
    """Run full Stockfish analysis on a user_game, returning per-move evals
    and accuracy. Results are cached in ``game_analyses``."""
    row = await db.fetchrow(
        "SELECT * FROM user_games WHERE id = $1", game_id,
    )
    if row is None:
        raise ValueError(f"game {game_id} not found")

    # Check for cached analysis.
    cached = await db.fetchrow(
        "SELECT analysis_json FROM game_analyses WHERE user_game_id = $1", game_id,
    )
    if cached:
        return json.loads(cached["analysis_json"])

    pgn_game = chess.pgn.read_game(io.StringIO(row["pgn"]))
    if pgn_game is None:
        raise ValueError(f"unparseable PGN for game {game_id}")

    pool_owned = pool is None
    pool = pool or StockfishPool(size=1)
    try:
        moves_data = await _analyse_moves(pgn_game, row["user_color"], pool)
    finally:
        if pool_owned:
            await pool.close()

    # Aggregate.
    all_acc = [m["accuracy"] for m in moves_data if m["is_user_move"]]
    avg_accuracy = sum(all_acc) / len(all_acc) if all_acc else 0.0

    # Phase breakdown: opening (ply 0-20), middlegame (20-60), endgame (60+).
    phase_acc: dict[str, list[float]] = {"opening": [], "middlegame": [], "endgame": []}
    for m in moves_data:
        if not m["is_user_move"]:
            continue
        if m["ply"] < 20:
            phase_acc["opening"].append(m["accuracy"])
        elif m["ply"] < 60:
            phase_acc["middlegame"].append(m["accuracy"])
        else:
            phase_acc["endgame"].append(m["accuracy"])
    phases = {
        k: (sum(v) / len(v) if v else 0.0) for k, v in phase_acc.items()
    }

    # Classify mistakes.
    blunders = [m for m in moves_data if m["is_user_move"] and m["swing"] >= 300]
    mistakes = [m for m in moves_data if m["is_user_move"] and 100 <= m["swing"] < 300]
    inaccuracies = [m for m in moves_data if m["is_user_move"] and 50 <= m["swing"] < 100]

    result = {
        "game_id": game_id,
        "user_color": row["user_color"],
        "result": row["result"],
        "played_at": str(row["played_at"]),
        "eco_code": row["eco_code"],
        "opening_name": row["opening_name"],
        "opponent_name": row["opponent_name"],
        "avg_accuracy": avg_accuracy,
        "phases": phases,
        "blunders": len(blunders),
        "mistakes": len(mistakes),
        "inaccuracies": len(inaccuracies),
        "moves": moves_data,
    }

    # Cache.
    await db.execute(
        """
        INSERT INTO game_analyses (user_game_id, user_id, accuracy, phases,
                                   blunders, mistakes, inaccuracies, analysis_json)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (user_game_id) DO UPDATE SET
            accuracy = EXCLUDED.accuracy,
            phases = EXCLUDED.phases,
            analysis_json = EXCLUDED.analysis_json
        """,
        game_id,
        row["user_id"],
        avg_accuracy,
        json.dumps(phases),
        len(blunders),
        len(mistakes),
        len(inaccuracies),
        json.dumps(result, default=str),
    )
    return result


async def _analyse_moves(
    pgn_game: chess.pgn.Game,
    user_color: str,
    pool: StockfishPool,
) -> list[dict[str, Any]]:
    board = pgn_game.board()
    user_white = user_color == "white"
    moves_data: list[dict[str, Any]] = []
    prev_cp = 0

    for ply, move in enumerate(pgn_game.mainline_moves()):
        is_user = (board.turn == chess.WHITE) == user_white

        infos = await pool.analyse(board, depth=18, multipv=1)
        cp_before = score_to_cp(infos[0]["score"].pov(chess.WHITE)) if infos else 0
        best_pv = infos[0].get("pv", []) if infos else []
        best_move = best_pv[0] if best_pv else None

        board.push(move)
        infos_after = await pool.analyse(board, depth=18, multipv=1)
        cp_after = score_to_cp(infos_after[0]["score"].pov(chess.WHITE)) if infos_after else 0

        if is_user:
            # Swing from user's perspective.
            if user_white:
                swing = cp_before - cp_after
            else:
                swing = cp_after - cp_before
            swing = max(0, swing)
            accuracy = max(0.0, 100.0 - swing / 10.0)
        else:
            swing = 0
            accuracy = 100.0

        moves_data.append({
            "ply": ply,
            "move_number": ply // 2 + 1,
            "move_uci": move.uci(),
            "is_user_move": is_user,
            "cp_before": cp_before,
            "cp_after": cp_after,
            "swing": swing,
            "accuracy": accuracy,
            "best_uci": best_move.uci() if best_move and is_user else None,
        })
        prev_cp = cp_after

    return moves_data


# ── Aggregated insights ─────────────────────────────────────────────────

async def get_user_insights(user_id: int, months_back: int = 12) -> dict[str, Any]:
    """Aggregate analysis data across all of a user's analysed games."""
    rows = await db.fetch(
        """
        SELECT ga.*, ug.played_at, ug.eco_code, ug.opening_name,
               ug.result, ug.opponent_name, ug.user_rating, ug.opponent_rating,
               ug.time_class, ug.user_color
        FROM game_analyses ga
        JOIN user_games ug ON ug.id = ga.user_game_id
        WHERE ga.user_id = $1
          AND ug.played_at >= NOW() - ($2 || ' months')::interval
        ORDER BY ug.played_at ASC
        """,
        user_id,
        str(months_back),
    )

    if not rows:
        return {
            "total_games": 0,
            "avg_accuracy": 0,
            "wins": 0, "draws": 0, "losses": 0,
            "accuracy_over_time": [],
            "phases": {"opening": 0, "middlegame": 0, "endgame": 0},
            "opening_performance": [],
            "mistake_counts": {"blunders": 0, "mistakes": 0, "inaccuracies": 0},
            "games": [],
            "coaching_summary": None,
        }

    total = len(rows)
    accs = [float(r["accuracy"]) for r in rows]
    avg_accuracy = sum(accs) / total

    wins = sum(1 for r in rows if r["result"] in ("win", "1-0", "0-1") and
               ((r["result"] == "1-0" and r["user_color"] == "white") or
                (r["result"] == "0-1" and r["user_color"] == "black") or
                r["result"] == "win"))
    draws = sum(1 for r in rows if r["result"] in ("draw", "1/2-1/2"))
    losses = total - wins - draws

    # Accuracy over time.
    accuracy_over_time = [
        {"date": str(r["played_at"])[:10], "accuracy": float(r["accuracy"]),
         "game_id": int(r["user_game_id"])}
        for r in rows
    ]

    # Phase averages.
    phase_totals: dict[str, list[float]] = {"opening": [], "middlegame": [], "endgame": []}
    for r in rows:
        phases = json.loads(r["phases"]) if isinstance(r["phases"], str) else (r["phases"] or {})
        for k in phase_totals:
            if k in phases and phases[k]:
                phase_totals[k].append(float(phases[k]))
    phases_avg = {k: (sum(v) / len(v) if v else 0.0) for k, v in phase_totals.items()}

    # Opening performance.
    opening_map: dict[str, dict[str, Any]] = {}
    for r in rows:
        eco = r["eco_code"] or "?"
        name = r["opening_name"] or eco
        key = eco
        if key not in opening_map:
            opening_map[key] = {
                "eco_code": eco, "opening_name": name, "games": 0,
                "wins": 0, "total_accuracy": 0.0,
            }
        opening_map[key]["games"] += 1
        opening_map[key]["total_accuracy"] += float(r["accuracy"])
        is_win = (
            (r["result"] == "1-0" and r["user_color"] == "white") or
            (r["result"] == "0-1" and r["user_color"] == "black") or
            r["result"] == "win"
        )
        if is_win:
            opening_map[key]["wins"] += 1

    opening_performance = sorted(
        [
            {
                "eco_code": v["eco_code"],
                "opening_name": v["opening_name"],
                "games": v["games"],
                "win_rate": v["wins"] / v["games"] if v["games"] else 0,
                "avg_accuracy": v["total_accuracy"] / v["games"] if v["games"] else 0,
            }
            for v in opening_map.values()
        ],
        key=lambda x: -x["games"],
    )

    # Mistake counts.
    total_blunders = sum(int(r["blunders"] or 0) for r in rows)
    total_mistakes = sum(int(r["mistakes"] or 0) for r in rows)
    total_inaccuracies = sum(int(r["inaccuracies"] or 0) for r in rows)

    # Game list (summary).
    games_list = [
        {
            "game_id": int(r["user_game_id"]),
            "date": str(r["played_at"])[:10],
            "opponent": r["opponent_name"],
            "result": r["result"],
            "accuracy": float(r["accuracy"]),
            "eco_code": r["eco_code"],
            "opening_name": r["opening_name"],
            "blunders": int(r["blunders"] or 0),
            "mistakes": int(r["mistakes"] or 0),
        }
        for r in rows
    ]

    return {
        "total_games": total,
        "avg_accuracy": avg_accuracy,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "accuracy_over_time": accuracy_over_time,
        "phases": phases_avg,
        "opening_performance": opening_performance[:20],
        "mistake_counts": {
            "blunders": total_blunders,
            "mistakes": total_mistakes,
            "inaccuracies": total_inaccuracies,
        },
        "games": games_list,
        "coaching_summary": None,
    }


async def generate_insight_summary(user_id: int) -> str:
    """Generate an LLM coaching summary from aggregated insights."""
    insights = await get_user_insights(user_id)
    if insights["total_games"] == 0:
        return "No games analysed yet. Import and analyse your games to get coaching insights."

    user_row = await db.fetchrow("SELECT username FROM users WHERE id = $1", user_id)
    username = user_row["username"] if user_row else "Player"

    weak_openings = "\n".join(
        f"  - {o['eco_code']} {o['opening_name']}: {o['games']} games, "
        f"{o['win_rate']*100:.0f}% win rate, {o['avg_accuracy']:.1f}% accuracy"
        for o in sorted(insights["opening_performance"], key=lambda x: x["avg_accuracy"])[:3]
    ) or "  (not enough data)"

    mc = insights["mistake_counts"]
    mistake_types = (
        f"  Blunders: {mc['blunders']} | Mistakes: {mc['mistakes']} | "
        f"Inaccuracies: {mc['inaccuracies']}"
    )

    # Rating trend from accuracy_over_time.
    aot = insights["accuracy_over_time"]
    if len(aot) >= 4:
        first_half = sum(a["accuracy"] for a in aot[: len(aot) // 2]) / (len(aot) // 2)
        second_half = sum(a["accuracy"] for a in aot[len(aot) // 2 :]) / (len(aot) - len(aot) // 2)
        trend = f"{second_half - first_half:+.1f}% accuracy change (recent vs earlier)"
    else:
        trend = "Not enough data for trend"

    prompt = PROMPTS["insight_summary"]
    user_text = prompt.user.format(
        username=username,
        total_games=insights["total_games"],
        avg_accuracy=insights["avg_accuracy"],
        wins=insights["wins"],
        draws=insights["draws"],
        losses=insights["losses"],
        opening_accuracy=insights["phases"].get("opening", 0),
        middlegame_accuracy=insights["phases"].get("middlegame", 0),
        endgame_accuracy=insights["phases"].get("endgame", 0),
        weak_openings=weak_openings,
        mistake_types=mistake_types,
        rating_trend=trend,
    )

    client = get_client()
    return await client.chat(
        [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user_text},
        ],
        temperature=0.3,
    )


# ── Batch analysis ──────────────────────────────────────────────────────

async def analyse_all_games(user_id: int) -> int:
    """Analyse all un-analysed games for a user. Returns count of newly analysed."""
    rows = await db.fetch(
        """
        SELECT ug.id FROM user_games ug
        LEFT JOIN game_analyses ga ON ga.user_game_id = ug.id
        WHERE ug.user_id = $1 AND ga.id IS NULL
        ORDER BY ug.played_at DESC
        LIMIT 200
        """,
        user_id,
    )
    pool = StockfishPool(size=2)
    count = 0
    try:
        for r in rows:
            try:
                await analyse_game(int(r["id"]), pool)
                count += 1
            except Exception:
                pass
    finally:
        await pool.close()
    return count
