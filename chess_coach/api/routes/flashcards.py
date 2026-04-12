"""Flashcard endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...fsrs_cards.generator import generate_cards_for_opening
from ...fsrs_cards.scheduler import get_due_cards, review_card


router = APIRouter()


class GenerateRequest(BaseModel):
    user_id: int
    opening_node_id: int
    n: int = Field(default=6, ge=1, le=20)


class ReviewRequest(BaseModel):
    user_id: int
    rating: int = Field(ge=1, le=4)
    review_duration_ms: int | None = None


@router.post("/generate")
async def generate(req: GenerateRequest) -> dict:
    try:
        ids = await generate_cards_for_opening(
            user_id=req.user_id,
            opening_node_id=req.opening_node_id,
            n=req.n,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"created": ids}


@router.get("/due")
async def due(user_id: int, limit: int = 20) -> dict:
    return {"cards": await get_due_cards(user_id, limit=limit)}


@router.post("/{card_id}/review")
async def review(card_id: int, req: ReviewRequest) -> dict:
    try:
        state = await review_card(
            card_id=card_id,
            rating_int=req.rating,
            user_id=req.user_id,
            review_duration_ms=req.review_duration_ms,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "state": state.state,
        "stability": state.stability,
        "difficulty": state.difficulty,
        "due": state.due.isoformat(),
    }
