"""Real-time Stockfish analysis endpoint.

Accepts a FEN and returns multi-PV engine lines with centipawn evaluations.
Used by the frontend for the evaluation bar, opening explorer analysis,
and engine arrows overlay.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

import chess

from ...config import settings
from ...engine.stockfish import StockfishPool, score_to_cp

router = APIRouter()

# Shared pool — initialised lazily on first request.
_pool: StockfishPool | None = None


async def _get_pool() -> StockfishPool:
    global _pool
    if _pool is None:
        _pool = StockfishPool(size=2)
    return _pool


class AnalyseRequest(BaseModel):
    fen: str
    depth: int | None = Field(default=None, ge=1, le=40)
    multipv: int | None = Field(default=None, ge=1, le=5)
    nodes: int | None = Field(default=None, ge=1000)


class AnalysisLine(BaseModel):
    pv: list[str]
    cp: int | None = None
    mate: int | None = None
    depth: int


class AnalyseResponse(BaseModel):
    fen: str
    lines: list[AnalysisLine]


@router.post("", response_model=AnalyseResponse)
async def analyse(req: AnalyseRequest) -> AnalyseResponse:
    board = chess.Board(req.fen)
    depth = req.depth or settings.stockfish_analysis_depth
    multipv = req.multipv or settings.stockfish_analysis_multipv
    pool = await _get_pool()

    infos = await pool.analyse(
        board,
        depth=depth,
        nodes=req.nodes,
        multipv=multipv,
    )

    lines: list[AnalysisLine] = []
    for info in infos:
        pv_moves = [m.uci() for m in info.get("pv", [])]
        score = info.get("score")
        cp = None
        mate = None
        if score is not None:
            pov = score.pov(board.turn)
            m = pov.mate()
            if m is not None:
                mate = m
            else:
                cp = score_to_cp(pov)
        lines.append(AnalysisLine(
            pv=pv_moves,
            cp=cp,
            mate=mate,
            depth=info.get("depth", depth),
        ))

    return AnalyseResponse(fen=req.fen, lines=lines)
