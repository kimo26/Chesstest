"""Insights API routes — retroactive game analysis and aggregated statistics."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from ...analysis.insights import (
    analyse_all_games,
    analyse_game,
    generate_insight_summary,
    get_user_insights,
)

router = APIRouter()


@router.get("/{user_id}")
async def insights(user_id: int, months_back: int = 12):
    data = await get_user_insights(user_id, months_back)
    return data


@router.post("/{user_id}/analyse-all")
async def analyse_all(user_id: int, background_tasks: BackgroundTasks):
    """Kick off batch analysis in the background."""
    background_tasks.add_task(analyse_all_games, user_id)
    return {"status": "started", "message": "Batch analysis started in background."}


@router.get("/{user_id}/game/{game_id}")
async def game_analysis(user_id: int, game_id: int):
    data = await analyse_game(game_id)
    return data


@router.post("/{user_id}/coaching-summary")
async def coaching_summary(user_id: int):
    summary = await generate_insight_summary(user_id)
    return {"summary": summary}
