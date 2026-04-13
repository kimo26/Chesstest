"""Hydrate the RAG corpus from Wikipedia (via Wikidata), not from the LLM.

The original design had the local LLM invent opening descriptions. We
replaced that with a Wikipedia-sourced pipeline so every claim in the
corpus is grounded in real, verifiable text. The LLM is still used, but
only for closed tasks that don't risk hallucination:

* normalising the Wikipedia extract into a clean coaching paragraph
  (pure rewrite — must not add facts),
* extracting structured metadata (themes, plans, key squares) from the
  extract.

Flow per opening_node:

1. Look up the matching Wikipedia article via ``data.wikidata.find_for_node``
   (ECO + name fuzzy match).
2. Slice the extract into the relevant section (the whole article for
   family nodes, the relevant variation section for deeper nodes).
3. Write the extract verbatim to ``opening_nodes.description`` with an
   attribution suffix. Store the Wikidata Q-id + revision in metadata so
   the RAG retriever can cite it.
4. Optionally ask the LLM for structured metadata — this is the only LLM
   call, and it's given the Wikipedia text as its single source of truth.
5. Chunk and embed.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Sequence

import chess

from .. import db
from ..config import settings
from ..data.wikidata import find_for_node
from ..engine.stockfish import StockfishPool, score_to_cp
from ..llm import get_client
from ..llm.prompts import PROMPTS
from .chunker import Chunk, build_chunks_for_node


# ----------------------------------------------------------------------------
# Section slicing
# ----------------------------------------------------------------------------

_SECTION_RE = re.compile(r"(?m)^==+\s*(?P<title>.+?)\s*==+\s*$")


def _slice_extract_for_node(extract: str, node: dict[str, Any]) -> str:
    """Return the most relevant chunk of a Wikipedia extract for a node.

    For a depth-0/1 node (e.g. "Sicilian Defence") we take the entire
    article introduction plus the first few top-level sections. For a
    deeper variation node (e.g. "Sicilian Defence, Najdorf Variation") we
    search for a section heading that mentions the variation name and
    return that section; if not found, we fall back to the introduction.
    """
    if not extract:
        return ""

    depth = node.get("depth") or 0
    name = (node.get("opening_name") or "").lower()

    if depth <= 1:
        # Intro + first ~4 sections, hard-capped at ~3500 chars so chunk
        # embeddings stay well under the model's context window.
        return extract[:3500]

    # Walk the extract looking for a heading that matches the variation name.
    headings = [(m.start(), m.end(), m.group("title")) for m in _SECTION_RE.finditer(extract)]
    if not headings:
        return extract[:2000]

    # Variation keywords to search for within headings.
    keywords = [
        w for w in re.split(r"[,\s:]+", name)
        if len(w) > 3 and w not in {"defence", "defense", "opening", "variation", "system", "attack"}
    ]

    best: tuple[int, int] | None = None  # (start, end) indices in extract
    for i, (start, end, title) in enumerate(headings):
        lowered = title.lower()
        if any(k in lowered for k in keywords):
            section_start = end
            section_end = headings[i + 1][0] if i + 1 < len(headings) else len(extract)
            best = (section_start, section_end)
            break

    if best is None:
        return extract[: headings[0][0]] or extract[:2000]
    return extract[best[0] : best[1]].strip()[:3500]


# ----------------------------------------------------------------------------
# DB helpers
# ----------------------------------------------------------------------------

async def _load_node(node_id: int) -> dict[str, Any] | None:
    row = await db.fetchrow(
        """
        SELECT o.*, p.opening_name AS parent_name, p.move_sequence AS parent_moves
        FROM opening_nodes o
        LEFT JOIN opening_nodes p ON p.id = o.parent_id
        WHERE o.id = $1
        """,
        node_id,
    )
    return dict(row) if row else None


async def _write_node_description(
    node_id: int,
    description: str,
    metadata: dict[str, Any],
    source: dict[str, Any],
) -> None:
    await db.execute(
        """
        UPDATE opening_nodes
           SET description   = $1,
               themes        = $2,
               typical_plans = $3,
               key_squares   = $4,
               updated_at    = NOW()
         WHERE id = $5
        """,
        description,
        metadata.get("themes") or [],
        json.dumps(metadata.get("typical_plans") or {}),
        metadata.get("key_squares") or [],
        node_id,
    )


# ----------------------------------------------------------------------------
# Wikipedia-sourced description
# ----------------------------------------------------------------------------

async def fetch_description(node: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Look up a Wikipedia-sourced description for an opening node.

    Returns ``(description_text, source_metadata)`` or ``None`` if no
    matching Wikipedia article exists. ``description_text`` ends with an
    attribution line pointing at the Wikipedia article.
    """
    wiki = await find_for_node(node)
    if wiki is None or not wiki.get("extract"):
        return None

    section = _slice_extract_for_node(wiki["extract"], node)
    if not section:
        return None

    attribution = (
        f"\n\nSource: Wikipedia — {wiki['title']} ({wiki['wikipedia_url']}), "
        "CC BY-SA 4.0."
    )
    return (
        section + attribution,
        {
            "wiki_id": wiki["id"],
            "wikidata_qid_article": wiki["title"],
            "wikipedia_url": wiki["wikipedia_url"],
            "wiki_revision": wiki.get("revision_id"),
            "eco_matched": wiki.get("eco_code"),
        },
    )


# ----------------------------------------------------------------------------
# LLM metadata (closed summarisation of the Wikipedia text)
# ----------------------------------------------------------------------------

async def generate_metadata(node: dict[str, Any], description: str) -> dict[str, Any]:
    client = get_client()
    prompt = PROMPTS["opening_metadata"]
    user_text = prompt.user.format(
        opening_name=node.get("opening_name") or "—",
        move_sequence=node.get("move_sequence") or "—",
        description=description,
    )
    try:
        return await client.generate_json(
            prompt.system,
            user_text,
            model=settings.ollama_fast_model,
            temperature=0.1,
        )
    except Exception:
        return {}


# ----------------------------------------------------------------------------
# Chunking + embedding
# ----------------------------------------------------------------------------

async def upsert_chunks(
    node_id: int,
    chunks: Sequence[Chunk],
    *,
    source: dict[str, Any] | None = None,
) -> None:
    if not chunks:
        return

    client = get_client()
    vectors = await client.embed_batched([c.content for c in chunks], batch_size=16)

    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM rag_documents WHERE opening_node_id = $1 AND source_type IN ('wikipedia','llm_generated')",
                node_id,
            )
            for chunk, vec in zip(chunks, vectors):
                meta = dict(chunk.metadata)
                if source:
                    meta["source"] = source
                await conn.execute(
                    """
                    INSERT INTO rag_documents (
                        opening_node_id, chunk_level, title, content, embedding,
                        metadata, source_type, source_model
                    ) VALUES ($1,$2,$3,$4,$5::vector,$6,$7,$8)
                    """,
                    node_id,
                    chunk.chunk_level,
                    chunk.title,
                    chunk.content,
                    db.vector_literal(vec),
                    json.dumps(meta),
                    "wikipedia" if source else "unknown",
                    "wikipedia" if source else None,
                )


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------

async def _stockfish_enrichment(fen: str) -> str:
    """Run Stockfish on a FEN and return a human-readable eval paragraph."""
    try:
        board = chess.Board(fen)
        pool = StockfishPool(size=1)
        try:
            infos = await pool.analyse(board, depth=30, multipv=3)
        finally:
            await pool.close()

        if not infos:
            return ""

        lines_text = []
        for i, info in enumerate(infos, 1):
            pv = info.get("pv", [])
            if not pv:
                continue
            score = info.get("score")
            if score is None:
                continue
            pov = score.pov(chess.WHITE)
            mate = pov.mate()
            if mate is not None:
                eval_str = f"M{mate}"
            else:
                cp = score_to_cp(pov)
                eval_str = f"{cp/100:+.1f}"
            san_moves = []
            temp = board.copy()
            for m in pv[:6]:
                san_moves.append(temp.san(m))
                temp.push(m)
            lines_text.append(f"{i}. {' '.join(san_moves)} ({eval_str})")

        if not lines_text:
            return ""

        return (
            "\n\nEngine evaluation (Stockfish 17, depth 30):\n"
            + "\n".join(lines_text)
        )
    except Exception:
        return ""


async def generate_for_node(node_id: int) -> bool:
    node = await _load_node(node_id)
    if node is None:
        return False

    result = await fetch_description(node)
    if result is None:
        # No Wikipedia match — we deliberately do NOT fall back to LLM
        # generation, because the whole point of this path is to avoid
        # inventing facts. The node simply stays un-described.
        return False

    description, source_meta = result

    # Enrich with Stockfish evaluation.
    sf_text = await _stockfish_enrichment(node.get("fen", ""))
    if sf_text:
        description += sf_text

    metadata = await generate_metadata(node, description)
    await _write_node_description(node_id, description, metadata, source_meta)

    enriched = {
        **node,
        "description": description,
        **{k: metadata.get(k) for k in ("themes", "typical_plans", "key_squares")},
    }
    chunks = build_chunks_for_node(enriched)
    await upsert_chunks(node_id, chunks, source=source_meta)
    return True


async def generate_missing(
    *,
    limit: int | None = None,
    min_total_games: int = 500,
    concurrency: int = 4,
) -> int:
    rows = await db.fetch(
        """
        SELECT id FROM opening_nodes
        WHERE description IS NULL
          AND opening_name IS NOT NULL
          AND COALESCE(total_games, 0) >= $1
        ORDER BY COALESCE(total_games, 0) DESC
        LIMIT COALESCE($2, 100000)
        """,
        min_total_games,
        limit,
    )

    sem = asyncio.Semaphore(concurrency)
    count = 0

    async def worker(nid: int) -> None:
        nonlocal count
        async with sem:
            try:
                if await generate_for_node(nid):
                    count += 1
            except Exception:
                pass

    await asyncio.gather(*(worker(int(r["id"])) for r in rows))
    return count
