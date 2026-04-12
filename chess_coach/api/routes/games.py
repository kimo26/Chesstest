"""Game import + analysis endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ...analysis.weakness import recompute_weaknesses, repertoire_gaps
from ...data.chesscom_importer import import_user_games, import_opponent_games


router = APIRouter()


class ImportRequest(BaseModel):
    user_id: int
    chess_com_username: str
    months_back: int = 12


class OpponentImportRequest(BaseModel):
    opponent: str
    months_back: int = 24


@router.post("/import")
async def import_games(req: ImportRequest) -> dict:
    inserted = await import_user_games(
        user_id=req.user_id,
        chess_com_username=req.chess_com_username,
        months_back=req.months_back,
    )
    return {"inserted": inserted}


@router.post("/import-opponent")
async def import_opponent(req: OpponentImportRequest) -> dict:
    inserted = await import_opponent_games(
        opponent=req.opponent,
        months_back=req.months_back,
    )
    return {"inserted": inserted}


@router.post("/weaknesses/{user_id}/recompute")
async def recompute(user_id: int) -> dict:
    n = await recompute_weaknesses(user_id)
    return {"updated": n}


@router.get("/weaknesses/{user_id}")
async def weaknesses(user_id: int, limit: int = 10) -> dict:
    return {"weaknesses": await repertoire_gaps(user_id, limit=limit)}
