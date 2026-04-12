"""Background training endpoints (maia-individual fine-tuning)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...maia.individual_trainer import train_individual_model


router = APIRouter()


class TrainRequest(BaseModel):
    opponent: str
    steps: int = 4000
    batch_size: int = 256
    learning_rate: float = 1e-4
    min_games: int = 20


_jobs: dict[str, asyncio.Task] = {}


@router.post("/maia-individual")
async def train_maia(req: TrainRequest) -> dict:
    """Kick off a fine-tune. Returns immediately with a job id."""
    if req.opponent in _jobs and not _jobs[req.opponent].done():
        return {"status": "already_running"}

    async def runner() -> None:
        try:
            await train_individual_model(
                opponent=req.opponent,
                steps=req.steps,
                batch_size=req.batch_size,
                learning_rate=req.learning_rate,
                min_games=req.min_games,
            )
        finally:
            _jobs.pop(req.opponent, None)

    _jobs[req.opponent] = asyncio.create_task(runner())
    return {"status": "started", "opponent": req.opponent}


@router.get("/maia-individual/{opponent}")
async def training_status(opponent: str) -> dict:
    task = _jobs.get(opponent)
    if task is None:
        return {"status": "idle"}
    if task.done():
        if task.exception():
            return {"status": "failed", "error": str(task.exception())}
        return {"status": "done"}
    return {"status": "running"}
