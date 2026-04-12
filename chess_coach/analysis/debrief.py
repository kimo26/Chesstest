"""Post-game debrief generation.

Given a finished game (PGN + list of critical moments from Stockfish) we:

1. Identify the opening that was played.
2. Retrieve a small bundle of RAG passages for that opening — drawn from
   Wikipedia-sourced content, so the debrief is grounded in real theory.
3. Prompt the local LLM for a structured debrief.

The LLM is allowed to discuss the moves of the specific game because those
are verifiable from the PGN, but every reference to opening theory must
come from the retrieved Wikipedia passages (the prompt makes that explicit).
"""
from __future__ import annotations

import json
from typing import Any

from .. import db
from ..llm import get_client
from ..llm.prompts import PROMPTS
from ..rag.retriever import hybrid_search


async def _opening_context(opening_node_id: int | None) -> tuple[str, str, str]:
    if opening_node_id is None:
        return "—", "—", "(no opening identified)"
    row = await db.fetchrow(
        "SELECT opening_name, eco_code, description FROM opening_nodes WHERE id = $1",
        opening_node_id,
    )
    if row is None:
        return "—", "—", "(no opening found)"
    return row["opening_name"] or "—", row["eco_code"] or "—", row["description"] or ""


async def generate_debrief(
    *,
    pgn: str,
    user_color: str,
    opening_node_id: int | None,
    result: str,
    critical_moments: list[dict[str, Any]],
) -> str:
    opening_name, eco_code, description = await _opening_context(opening_node_id)

    # Retrieve a few grounded passages for the opening.
    rag_results = await hybrid_search(
        query=f"{opening_name} strategic plan tactics",
        top_k=4,
        filter_opening_node_id=opening_node_id,
    )
    theory = "\n\n".join(f"[#{i+1}] {r.content}" for i, r in enumerate(rag_results))
    if not theory and description:
        theory = description

    prompt = PROMPTS["debrief"]
    user_text = prompt.user.format(
        opening_name=opening_name,
        eco_code=eco_code,
        result=result,
        user_color=user_color,
        pgn=pgn,
        critical_moments=json.dumps(critical_moments, indent=2),
        theory=theory or "(no theory available)",
    )

    client = get_client()
    return await client.chat(
        [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user_text},
        ],
        temperature=0.3,
    )
