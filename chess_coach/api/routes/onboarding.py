"""First-run onboarding + data-pipeline orchestration.

When a user lands on the app for the first time they haven't imported any
games, loaded the ECO tree, refreshed the Wikipedia corpus, or ingested
the knowledge base. The frontend redirects to ``/onboarding`` while this
module's background task runs every pipeline in sequence and streams
progress (current step, elapsed seconds, ETA) through
``GET /api/onboarding/status``.

Each step updates ``onboarding_state.current_step`` and appends a row to
``steps_done`` so the frontend can render a checklist with per-step
progress. If a step fails we record the error and keep going — the user
ends up with a partially-populated app rather than a dead wizard.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import db


router = APIRouter()


# ──────────────────────────────────────────────────────────────────────
#  Step catalogue
# ──────────────────────────────────────────────────────────────────────

# (name, human label, rough ETA seconds). ETAs are worst-case hints — the
# live UI replaces them with the elapsed timer once a step is running.
STEPS: list[tuple[str, str, int]] = [
    ("eco",          "Load ECO opening codes",                     30),
    ("wiki",         "Refresh Wikipedia opening corpus",           90),
    ("knowledge",    "Ingest knowledge base (PDFs, Wikibooks)",    600),
    ("import_games", "Import Chess.com games + opponent history",  180),
    ("descriptions", "Generate coach descriptions for openings",   900),
]


# ──────────────────────────────────────────────────────────────────────
#  State helpers
# ──────────────────────────────────────────────────────────────────────

async def _load_state(user_id: int) -> dict | None:
    row = await db.fetchrow(
        """
        SELECT user_id, chesscom_username, pipelines_started_at,
               pipelines_completed_at, current_step, steps_done, last_error
        FROM onboarding_state WHERE user_id = $1
        """,
        user_id,
    )
    if row is None:
        return None
    steps_done = row["steps_done"]
    if isinstance(steps_done, str):
        steps_done = json.loads(steps_done)
    return {
        "user_id": row["user_id"],
        "chesscom_username": row["chesscom_username"],
        "started_at": row["pipelines_started_at"],
        "completed_at": row["pipelines_completed_at"],
        "current_step": row["current_step"],
        "steps_done": steps_done or [],
        "last_error": row["last_error"],
    }


async def _init_state(user_id: int, chesscom_username: str) -> None:
    await db.execute(
        """
        INSERT INTO onboarding_state
            (user_id, chesscom_username, pipelines_started_at, current_step, steps_done)
        VALUES ($1, $2, NOW(), $3, '[]'::jsonb)
        ON CONFLICT (user_id) DO UPDATE SET
            chesscom_username    = EXCLUDED.chesscom_username,
            pipelines_started_at = NOW(),
            pipelines_completed_at = NULL,
            current_step         = EXCLUDED.current_step,
            steps_done           = '[]'::jsonb,
            last_error           = NULL,
            updated_at           = NOW()
        """,
        user_id,
        chesscom_username,
        STEPS[0][0],
    )


async def _set_step(user_id: int, step: str) -> None:
    await db.execute(
        "UPDATE onboarding_state SET current_step = $2, updated_at = NOW() WHERE user_id = $1",
        user_id,
        step,
    )


async def _finish_step(
    user_id: int,
    step: str,
    *,
    state: str,
    elapsed: float,
    detail: str | None = None,
    error: str | None = None,
) -> None:
    row = {
        "name": step,
        "state": state,
        "elapsed_seconds": round(elapsed, 1),
        "detail": detail,
        "error": error,
    }
    await db.execute(
        """
        UPDATE onboarding_state
        SET steps_done = steps_done || $2::jsonb,
            last_error = COALESCE($3, last_error),
            updated_at = NOW()
        WHERE user_id = $1
        """,
        user_id,
        json.dumps(row),
        error,
    )


async def _mark_complete(user_id: int) -> None:
    await db.execute(
        """
        UPDATE onboarding_state
        SET pipelines_completed_at = NOW(), current_step = NULL, updated_at = NOW()
        WHERE user_id = $1
        """,
        user_id,
    )


# ──────────────────────────────────────────────────────────────────────
#  The actual pipeline runner
# ──────────────────────────────────────────────────────────────────────

async def _run_pipelines(user_id: int, chess_com_username: str) -> None:
    """Run each onboarding step serially; record progress as we go."""
    # Imports are deferred so /api/onboarding/status is cheap even if the
    # heavy modules (pypdf, torch, etc.) take a moment to load.
    from ...data import eco as eco_mod
    from ...data.wikidata import refresh_wiki_openings
    from ...data import knowledge_ingest as ki
    from ...data.chesscom_importer import import_user_games

    # 1. ECO load (~30s)
    step = "eco"
    await _set_step(user_id, step)
    t0 = time.monotonic()
    try:
        entries = eco_mod.load_eco_tsv(Path("./data/chess-openings"))
        inserted = await eco_mod.upsert_eco_entries(entries)
        linked = await eco_mod.link_parents()
        await _finish_step(
            user_id, step, state="ok", elapsed=time.monotonic() - t0,
            detail=f"{inserted} openings, {linked} parent links",
        )
    except Exception as exc:
        await _finish_step(
            user_id, step, state="error", elapsed=time.monotonic() - t0,
            error=f"{type(exc).__name__}: {exc}",
        )

    # 2. Wiki refresh (~90s)
    step = "wiki"
    await _set_step(user_id, step)
    t0 = time.monotonic()
    try:
        n = await refresh_wiki_openings()
        await _finish_step(
            user_id, step, state="ok", elapsed=time.monotonic() - t0,
            detail=f"{n} Wikipedia articles",
        )
    except Exception as exc:
        await _finish_step(
            user_id, step, state="error", elapsed=time.monotonic() - t0,
            error=f"{type(exc).__name__}: {exc}",
        )

    # 3. Knowledge ingest — PDF preset (chess-wisdom) + Wikibooks. Skip
    #    puzzles / annotated PGNs here since they need extra downloads.
    step = "knowledge"
    await _set_step(user_id, step)
    t0 = time.monotonic()
    ingest_detail = []
    try:
        try:
            n_wb = await ki.ingest_wikibooks(concurrency=4)
            ingest_detail.append(f"{n_wb} wikibooks pages")
        except Exception as exc:
            ingest_detail.append(f"wikibooks failed: {exc}")
        try:
            n_pdf = await ki.ingest_pdf_preset("chess-wisdom")
            ingest_detail.append(f"{n_pdf} PDF chunks")
        except Exception as exc:
            ingest_detail.append(f"pdf failed: {exc}")
        await _finish_step(
            user_id, step, state="ok", elapsed=time.monotonic() - t0,
            detail="; ".join(ingest_detail),
        )
    except Exception as exc:
        await _finish_step(
            user_id, step, state="error", elapsed=time.monotonic() - t0,
            error=f"{type(exc).__name__}: {exc}",
        )

    # 4. Chess.com import (games + opponent games) (~3-5 min)
    step = "import_games"
    await _set_step(user_id, step)
    t0 = time.monotonic()
    try:
        inserted = await import_user_games(
            user_id, chess_com_username, months_back=12, collect_opponents=True,
        )
        await _finish_step(
            user_id, step, state="ok", elapsed=time.monotonic() - t0,
            detail=f"{inserted} new games + opponent history",
        )
    except Exception as exc:
        await _finish_step(
            user_id, step, state="error", elapsed=time.monotonic() - t0,
            error=f"{type(exc).__name__}: {exc}",
        )

    # 5. Description generation (bounded) — we only hydrate the most
    # popular unwritten nodes to keep onboarding under ~15 min. The user
    # can re-run the full ``generate_descriptions`` script later.
    step = "descriptions"
    await _set_step(user_id, step)
    t0 = time.monotonic()
    try:
        from ...rag.generator import generate_for_node
        rows = await db.fetch(
            """
            SELECT id FROM opening_nodes
            WHERE description IS NULL AND total_games > 0
            ORDER BY total_games DESC
            LIMIT 30
            """,
        )
        wrote = 0
        for r in rows:
            try:
                await generate_for_node(int(r["id"]))
                wrote += 1
            except Exception:
                continue
        await _finish_step(
            user_id, step, state="ok", elapsed=time.monotonic() - t0,
            detail=f"wrote {wrote}/{len(rows)} descriptions",
        )
    except Exception as exc:
        await _finish_step(
            user_id, step, state="error", elapsed=time.monotonic() - t0,
            error=f"{type(exc).__name__}: {exc}",
        )

    await _mark_complete(user_id)


# ──────────────────────────────────────────────────────────────────────
#  In-process task registry (so we don't start the pipeline twice)
# ──────────────────────────────────────────────────────────────────────

_running_tasks: dict[int, asyncio.Task] = {}


async def _ensure_user(chess_com_username: str) -> int:
    """Return a user id for the given Chess.com username; create if needed."""
    row = await db.fetchrow(
        "SELECT id FROM users WHERE chess_com_user = $1 OR username = $1",
        chess_com_username,
    )
    if row is not None:
        return int(row["id"])
    row = await db.fetchrow(
        """
        INSERT INTO users (username, chess_com_user)
        VALUES ($1, $1)
        RETURNING id
        """,
        chess_com_username,
    )
    return int(row["id"])


# ──────────────────────────────────────────────────────────────────────
#  Routes
# ──────────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    chesscom_username: str


class StartResponse(BaseModel):
    user_id: int
    started: bool


class StepStatus(BaseModel):
    name: str
    label: str
    state: str               # "pending" | "running" | "ok" | "error"
    eta_seconds: int
    elapsed_seconds: float | None = None
    detail: str | None = None
    error: str | None = None


class OnboardingStatus(BaseModel):
    needs_onboarding: bool
    user_id: int | None = None
    chesscom_username: str | None = None
    in_progress: bool
    completed: bool
    steps: list[StepStatus]
    current_step: str | None = None


def _is_needed(state: dict | None, game_count: int) -> bool:
    """User needs onboarding if no pipelines ever completed and no games yet."""
    if state is None:
        return True
    if state.get("completed_at") is not None:
        return False
    return game_count == 0


@router.get("/status", response_model=OnboardingStatus)
async def status(user_id: int | None = None) -> OnboardingStatus:
    # When no explicit user_id, pick the most-recently-created user (single-
    # user local app assumption). Returns needs_onboarding=True if no users.
    if user_id is None:
        row = await db.fetchrow("SELECT id FROM users ORDER BY id DESC LIMIT 1")
        if row is None:
            return OnboardingStatus(
                needs_onboarding=True, in_progress=False, completed=False,
                steps=[
                    StepStatus(name=n, label=l, state="pending", eta_seconds=e)
                    for (n, l, e) in STEPS
                ],
            )
        user_id = int(row["id"])

    state = await _load_state(user_id)
    game_count = await db.fetchval(
        "SELECT COUNT(*) FROM user_games WHERE user_id = $1", user_id
    ) or 0

    done_by_name: dict[str, dict] = {}
    for rec in (state or {}).get("steps_done", []) or []:
        done_by_name[rec["name"]] = rec

    steps: list[StepStatus] = []
    current = (state or {}).get("current_step")
    for name, label, eta in STEPS:
        if name in done_by_name:
            rec = done_by_name[name]
            steps.append(StepStatus(
                name=name, label=label, eta_seconds=eta,
                state=rec.get("state") or "ok",
                elapsed_seconds=rec.get("elapsed_seconds"),
                detail=rec.get("detail"),
                error=rec.get("error"),
            ))
        elif current == name:
            steps.append(StepStatus(name=name, label=label, state="running", eta_seconds=eta))
        else:
            steps.append(StepStatus(name=name, label=label, state="pending", eta_seconds=eta))

    completed = bool(state and state.get("completed_at"))
    in_progress = bool(state and state.get("started_at") and not completed)
    return OnboardingStatus(
        needs_onboarding=_is_needed(state, int(game_count)),
        user_id=user_id,
        chesscom_username=(state or {}).get("chesscom_username"),
        in_progress=in_progress,
        completed=completed,
        steps=steps,
        current_step=current,
    )


@router.post("/start", response_model=StartResponse)
async def start(req: StartRequest) -> StartResponse:
    username = (req.chesscom_username or "").strip()
    if not username:
        raise HTTPException(400, "chesscom_username is required")

    user_id = await _ensure_user(username)

    # If a task is already running, don't double-start.
    task = _running_tasks.get(user_id)
    if task is not None and not task.done():
        return StartResponse(user_id=user_id, started=False)

    await _init_state(user_id, username)

    async def _runner() -> None:
        try:
            await _run_pipelines(user_id, username)
        except Exception as exc:
            await db.execute(
                "UPDATE onboarding_state SET last_error = $2, updated_at = NOW() WHERE user_id = $1",
                user_id,
                f"{type(exc).__name__}: {exc}",
            )

    _running_tasks[user_id] = asyncio.create_task(_runner())
    return StartResponse(user_id=user_id, started=True)
