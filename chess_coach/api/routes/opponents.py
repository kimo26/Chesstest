"""Opponents endpoint — feeds the practice-page opponent dropdown.

Rather than typing a Chess.com username, the user picks an opponent from
a ranked list of people they've actually played (and most often lost to).
The endpoint also reports, for each opponent, whether we already have a
fine-tuned maia-individual model so the UI can add a "model ready" badge.
"""
from __future__ import annotations

from fastapi import APIRouter

from ...data.chesscom_importer import recommended_opponents


router = APIRouter()


@router.get("/recommended/{user_id}")
async def recommended(user_id: int, limit: int = 20) -> dict:
    """Return the top ``limit`` opponents for ``user_id``, ranked by
    'need to practice' score (losses/draws/games weighted)."""
    items = await recommended_opponents(user_id, limit=limit)
    return {"opponents": items}
