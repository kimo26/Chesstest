"""Puzzle serving + attempt recording (with Glicko-2 updates)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import db


router = APIRouter()


# ----------------------------------------------------------------------------
# Minimal Glicko-2 implementation (no external dep)
# ----------------------------------------------------------------------------
# The full algorithm is ~30 lines; we keep everything inline here so the
# project has no hard dependency on a glicko2 pypi package.

_TAU = 0.5
_SCALE = 173.7178


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _e(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def glicko2_update(
    rating: float, rd: float, vol: float,
    opp_rating: float, opp_rd: float, score: float,
) -> tuple[float, float, float]:
    mu = (rating - 1500.0) / _SCALE
    phi = rd / _SCALE
    mu_j = (opp_rating - 1500.0) / _SCALE
    phi_j = opp_rd / _SCALE

    g_j = _g(phi_j)
    e_j = _e(mu, mu_j, phi_j)
    v = 1.0 / (g_j * g_j * e_j * (1.0 - e_j))
    delta = v * g_j * (score - e_j)

    a = math.log(vol * vol)

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2.0 * (phi * phi + v + ex) ** 2
        return num / den - (x - a) / (_TAU * _TAU)

    # Illinois algorithm
    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * _TAU) < 0:
            k += 1
        B = a - k * _TAU

    fA, fB = f(A), f(B)
    for _ in range(50):
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA = fA / 2.0
        B, fB = C, fC
        if abs(B - A) < 1e-6:
            break

    new_vol = math.exp(A / 2.0)
    phi_star = math.sqrt(phi * phi + new_vol * new_vol)
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    new_mu = mu + new_phi * new_phi * g_j * (score - e_j)

    return new_mu * _SCALE + 1500.0, new_phi * _SCALE, new_vol


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------

class AttemptRequest(BaseModel):
    user_id: int
    solved: bool
    moves_played: list[str] = Field(default_factory=list)
    time_spent_ms: int | None = None
    hint_used: bool = False


@router.get("/next/{user_id}")
async def next_puzzle(user_id: int, opening_node_id: int | None = None) -> dict:
    """Return the next puzzle for a user, within ±200 rating points of
    their current puzzle rating. If ``opening_node_id`` is supplied we
    restrict to puzzles tagged with that opening."""
    user = await db.fetchrow(
        "SELECT puzzle_rating FROM users WHERE id = $1", user_id
    )
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    r = float(user["puzzle_rating"])

    if opening_node_id is not None:
        row = await db.fetchrow(
            """
            SELECT * FROM puzzles
            WHERE opening_node_id = $1
              AND rating BETWEEN $2 AND $3
              AND NOT EXISTS (
                  SELECT 1 FROM puzzle_attempts pa
                   WHERE pa.puzzle_id = puzzles.id AND pa.user_id = $4
              )
            ORDER BY ABS(rating - $5) ASC
            LIMIT 1
            """,
            opening_node_id,
            r - 200,
            r + 200,
            user_id,
            r,
        )
    else:
        row = await db.fetchrow(
            """
            SELECT * FROM puzzles
            WHERE rating BETWEEN $1 AND $2
              AND NOT EXISTS (
                  SELECT 1 FROM puzzle_attempts pa
                   WHERE pa.puzzle_id = puzzles.id AND pa.user_id = $3
              )
            ORDER BY ABS(rating - $4) ASC
            LIMIT 1
            """,
            r - 200,
            r + 200,
            user_id,
            r,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="no puzzle available")
    return dict(row)


@router.post("/{puzzle_id}/attempt")
async def attempt(puzzle_id: int, req: AttemptRequest) -> dict:
    puzzle = await db.fetchrow("SELECT * FROM puzzles WHERE id = $1", puzzle_id)
    user = await db.fetchrow(
        "SELECT puzzle_rating, puzzle_rd, puzzle_vol FROM users WHERE id = $1",
        req.user_id,
    )
    if puzzle is None or user is None:
        raise HTTPException(status_code=404, detail="not found")

    score = 1.0 if req.solved else 0.0
    new_user = glicko2_update(
        float(user["puzzle_rating"]), float(user["puzzle_rd"]), float(user["puzzle_vol"]),
        float(puzzle["rating"]), float(puzzle["rating_rd"]), score,
    )
    new_puzzle = glicko2_update(
        float(puzzle["rating"]), float(puzzle["rating_rd"]), float(puzzle["rating_vol"]),
        float(user["puzzle_rating"]), float(user["puzzle_rd"]), 1.0 - score,
    )

    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE users SET puzzle_rating=$1, puzzle_rd=$2, puzzle_vol=$3 WHERE id=$4
                """,
                new_user[0], new_user[1], new_user[2], req.user_id,
            )
            await conn.execute(
                """
                UPDATE puzzles SET rating=$1, rating_rd=$2, rating_vol=$3,
                    attempts = attempts + 1,
                    solves   = solves + $4
                WHERE id=$5
                """,
                new_puzzle[0], new_puzzle[1], new_puzzle[2],
                1 if req.solved else 0, puzzle_id,
            )
            await conn.execute(
                """
                INSERT INTO puzzle_attempts (
                    puzzle_id, user_id, solved, moves_played, time_spent_ms,
                    hint_used, user_rating_before, user_rating_after,
                    puzzle_rating_before, puzzle_rating_after, attempted_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                puzzle_id, req.user_id, req.solved, req.moves_played,
                req.time_spent_ms, req.hint_used,
                float(user["puzzle_rating"]), new_user[0],
                float(puzzle["rating"]), new_puzzle[0],
                datetime.now(timezone.utc),
            )

    return {
        "user_rating": new_user[0],
        "puzzle_rating": new_puzzle[0],
    }
