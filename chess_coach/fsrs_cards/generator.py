"""Generate flashcards for an opening_node via the local LLM.

The LLM is prompted to return a JSON array of cards. Each card is validated
against the allowed card_type list, deduped against existing cards for the
same user+opening, and then inserted through the FSRS scheduler.
"""
from __future__ import annotations

import json
from typing import Any

from .. import db
from ..config import settings
from ..llm import get_client
from ..llm.prompts import PROMPTS
from .scheduler import create_card


ALLOWED_TYPES = {"move_quiz", "concept", "plan", "trap"}


async def generate_cards_for_opening(
    user_id: int,
    opening_node_id: int,
    *,
    n: int = 6,
    required_types: tuple[str, ...] = ("move_quiz", "concept", "plan"),
) -> list[int]:
    node = await db.fetchrow(
        "SELECT * FROM opening_nodes WHERE id = $1",
        opening_node_id,
    )
    if node is None:
        raise LookupError(f"opening_node {opening_node_id} not found")
    if not node["description"]:
        raise RuntimeError("Opening has no description yet — generate it first")

    prompt = PROMPTS["flashcards_for_opening"]
    user_text = prompt.user.format(
        n=n,
        eco_code=node["eco_code"] or "—",
        opening_name=node["opening_name"] or "—",
        move_sequence=node["move_sequence"] or "—",
        fen=node["fen"] or "—",
        description=node["description"],
        required_types=", ".join(required_types),
    )

    client = get_client()
    raw = await client.chat(
        [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user_text},
        ],
        model=settings.ollama_gen_model,
        temperature=0.4,
        json_mode=True,
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    # The LLM may return either {"cards": [...]} or a bare array.
    if isinstance(data, dict):
        cards = data.get("cards") or data.get("flashcards") or []
    else:
        cards = data
    if not isinstance(cards, list):
        return []

    inserted: list[int] = []
    existing_fronts = await _existing_fronts(user_id, opening_node_id)
    for c in cards:
        if not isinstance(c, dict):
            continue
        card_type = c.get("card_type")
        if card_type not in ALLOWED_TYPES:
            continue
        front = (c.get("front_text") or "").strip()
        back = (c.get("back_text") or "").strip()
        if not front or not back or front in existing_fronts:
            continue
        card_id = await create_card(
            user_id=user_id,
            opening_node_id=opening_node_id,
            fen=node["fen"],
            card_type=card_type,
            front_text=front,
            back_text=back,
            hint=c.get("hint") or None,
            tags=list(c.get("tags") or []),
            source_model=settings.ollama_gen_model,
        )
        inserted.append(card_id)
        existing_fronts.add(front)
    return inserted


async def _existing_fronts(user_id: int, opening_node_id: int) -> set[str]:
    rows = await db.fetch(
        "SELECT front_text FROM flashcards WHERE user_id=$1 AND opening_node_id=$2",
        user_id,
        opening_node_id,
    )
    return {r["front_text"] for r in rows}
