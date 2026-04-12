"""Weakness scoring and repertoire-gap analysis.

For every (user, opening_node, color) tuple, we aggregate the user's games
into wins/draws/losses, compute a weakness score, and upsert into
``user_weaknesses``. The score is high when the user is under-performing
that opening relative to the Lichess database baseline OR when they
repeatedly leave theory early (``deviation_ply``).
"""
from __future__ import annotations

from typing import Any

from .. import db


async def recompute_weaknesses(user_id: int) -> int:
    """Rebuild ``user_weaknesses`` for ``user_id`` from their games.

    Returns the number of weakness rows written.
    """
    rows = await db.fetch(
        """
        WITH g AS (
            SELECT opening_node_id, user_color,
                   COUNT(*) FILTER (WHERE result='win')  AS wins,
                   COUNT(*) FILTER (WHERE result='draw') AS draws,
                   COUNT(*) FILTER (WHERE result='loss') AS losses,
                   COUNT(*)                              AS games,
                   AVG(accuracy)                         AS avg_accuracy,
                   AVG(deviation_ply)                    AS avg_dev
            FROM user_games
            WHERE user_id = $1 AND opening_node_id IS NOT NULL
            GROUP BY opening_node_id, user_color
        ),
        joined AS (
            SELECT g.*, o.white_wins, o.draws AS db_draws, o.black_wins,
                   o.total_games
            FROM g JOIN opening_nodes o ON o.id = g.opening_node_id
        )
        SELECT *,
               CASE WHEN games > 0 THEN wins::float / games ELSE 0 END AS win_rate,
               CASE WHEN total_games > 0 AND user_color='white'
                    THEN white_wins::float / total_games
                    WHEN total_games > 0 AND user_color='black'
                    THEN black_wins::float / total_games
                    ELSE NULL END AS baseline_rate
        FROM joined
        """,
        user_id,
    )

    n = 0
    async with db.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                games = int(r["games"])
                if games == 0:
                    continue
                win_rate = float(r["win_rate"])
                baseline = r["baseline_rate"]
                delta = (win_rate - baseline) if baseline is not None else 0.0
                # Score combines (a) underperformance, (b) early theory drop
                # -- deviation_ply < 8 is a theory gap.
                dev = float(r["avg_dev"] or 20)
                score = max(0.0, -delta) * 0.7 + max(0.0, (15 - dev) / 15) * 0.3

                weakness_type = "theory_gap" if dev < 8 else (
                    "tactical" if (r["avg_accuracy"] or 100) < 75 else "positional"
                )

                await conn.execute(
                    """
                    INSERT INTO user_weaknesses (
                        user_id, opening_node_id, color,
                        games_played, wins, draws, losses,
                        avg_accuracy, avg_deviation_ply,
                        weakness_score, weakness_type, last_computed
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW())
                    ON CONFLICT (user_id, opening_node_id, color)
                    DO UPDATE SET
                        games_played = EXCLUDED.games_played,
                        wins         = EXCLUDED.wins,
                        draws        = EXCLUDED.draws,
                        losses       = EXCLUDED.losses,
                        avg_accuracy = EXCLUDED.avg_accuracy,
                        avg_deviation_ply = EXCLUDED.avg_deviation_ply,
                        weakness_score = EXCLUDED.weakness_score,
                        weakness_type  = EXCLUDED.weakness_type,
                        last_computed  = NOW()
                    """,
                    user_id,
                    int(r["opening_node_id"]),
                    r["user_color"],
                    games,
                    int(r["wins"]),
                    int(r["draws"]),
                    int(r["losses"]),
                    float(r["avg_accuracy"]) if r["avg_accuracy"] else None,
                    dev,
                    score,
                    weakness_type,
                )
                n += 1
    return n


async def repertoire_gaps(
    user_id: int,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the user's worst openings: underperforming vs. the DB baseline,
    with enough sample size to matter."""
    rows = await db.fetch(
        """
        SELECT w.opening_node_id, o.opening_name, o.eco_code,
               w.color, w.games_played, w.win_rate, w.avg_deviation_ply,
               w.weakness_score, w.weakness_type
        FROM user_weaknesses w
        JOIN opening_nodes o ON o.id = w.opening_node_id
        WHERE w.user_id = $1 AND w.games_played >= 3
        ORDER BY w.weakness_score DESC
        LIMIT $2
        """,
        user_id,
        limit,
    )
    return [dict(r) for r in rows]
