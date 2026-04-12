"""FastAPI entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .. import db
from ..llm import get_client
from ..maia.lc0_engine import shutdown_all_engines
from .routes import chat, flashcards, games, openings, practice, puzzles, training


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    try:
        yield
    finally:
        await shutdown_all_engines()
        await get_client().aclose()
        await db.close_pool()


app = FastAPI(title="Chess Coach", version="0.1.0", lifespan=lifespan)

app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(flashcards.router, prefix="/api/flashcards", tags=["flashcards"])
app.include_router(games.router, prefix="/api/games", tags=["games"])
app.include_router(openings.router, prefix="/api/openings", tags=["openings"])
app.include_router(practice.router, prefix="/api/practice", tags=["practice"])
app.include_router(puzzles.router, prefix="/api/puzzles", tags=["puzzles"])
app.include_router(training.router, prefix="/api/training", tags=["training"])


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
