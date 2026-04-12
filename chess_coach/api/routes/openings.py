"""Opening tree browsing + description lookup."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ... import db


router = APIRouter()


@router.get("/{node_id}")
async def get_opening(node_id: int) -> dict:
    row = await db.fetchrow(
        """
        SELECT id, parent_id, eco_code, opening_name, move_sequence, fen,
               depth, description, themes, typical_plans, key_squares,
               white_wins, draws, black_wins, total_games
        FROM opening_nodes WHERE id = $1
        """,
        node_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="opening not found")
    data = dict(row)

    children = await db.fetch(
        "SELECT id, opening_name, move_san, total_games FROM opening_nodes WHERE parent_id = $1 ORDER BY total_games DESC NULLS LAST LIMIT 20",
        node_id,
    )
    data["children"] = [dict(r) for r in children]
    return data


@router.get("/search/by-name")
async def search_by_name(q: str, limit: int = 20) -> dict:
    rows = await db.fetch(
        """
        SELECT id, eco_code, opening_name, move_sequence, depth, total_games
        FROM opening_nodes
        WHERE opening_name ILIKE '%' || $1 || '%'
        ORDER BY similarity(opening_name, $1) DESC, total_games DESC NULLS LAST
        LIMIT $2
        """,
        q,
        limit,
    )
    return {"results": [dict(r) for r in rows]}


@router.get("/by-fen/{fen:path}")
async def by_fen(fen: str) -> dict:
    canon = " ".join(fen.split()[:4])
    row = await db.fetchrow(
        "SELECT * FROM opening_nodes WHERE fen_canonical = $1",
        canon,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no opening for that fen")
    return dict(row)
