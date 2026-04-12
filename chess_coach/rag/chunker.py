"""Hierarchical chunker for opening-theory RAG.

For every opening node we emit up to three chunks:

* ``family`` (when depth == 0-2 and the node is named) — broad context about
  the opening as a whole. Retrieved for questions like "tell me about the
  Sicilian".
* ``variation`` — the main unit of retrieval. One chunk per named variation
  carrying the move sequence, FEN, stats, and the LLM-generated description.
* ``position`` — short chunks focused on a specific FEN / move explanation.
  Emitted when the description mentions a concrete move that deserves its
  own retrieval target.

Every chunk is prefixed with a context header that names the opening family,
variation, moves, and FEN, so that even a hit in the middle of a long
description carries the whole breadcrumb with it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


HEADER_TEMPLATE = (
    "[{family} ({eco})] > [{variation}]\n"
    "[Moves: {moves}]\n"
    "[FEN: {fen}]\n"
)


@dataclass
class Chunk:
    title: str
    content: str
    chunk_level: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _header(node: dict[str, Any]) -> str:
    return HEADER_TEMPLATE.format(
        family=node.get("parent_name") or node.get("opening_name") or "",
        eco=node.get("eco_code") or "",
        variation=node.get("opening_name") or "",
        moves=node.get("move_sequence") or "",
        fen=node.get("fen") or "",
    )


def _base_metadata(node: dict[str, Any]) -> dict[str, Any]:
    total = (node.get("white_wins") or 0) + (node.get("draws") or 0) + (node.get("black_wins") or 0)
    white_rate = (node.get("white_wins") or 0) / total if total else None
    return {
        "eco_code": node.get("eco_code"),
        "opening_name": node.get("opening_name"),
        "fen_canonical": " ".join((node.get("fen") or "").split()[:4]),
        "move_sequence": node.get("move_sequence"),
        "depth": node.get("depth"),
        "themes": node.get("themes") or [],
        "white_winrate": white_rate,
        "total_games": total,
        "parent_node_id": node.get("parent_id"),
    }


def build_chunks_for_node(node: dict[str, Any]) -> list[Chunk]:
    """Return a list of chunks to write to ``rag_documents`` for one node."""
    chunks: list[Chunk] = []
    description = (node.get("description") or "").strip()
    header = _header(node)
    meta = _base_metadata(node)

    # ---- variation chunk (primary retrieval unit) --------------------
    if description:
        chunks.append(
            Chunk(
                title=node.get("opening_name") or "Variation",
                content=header + "\n" + description,
                chunk_level="variation",
                metadata={**meta, "chunk_level": "variation"},
            )
        )

    # ---- family chunk (broad context) -------------------------------
    depth = node.get("depth") or 0
    if depth <= 2 and description:
        first_para = description.split("\n\n", 1)[0]
        chunks.append(
            Chunk(
                title=f"{node.get('opening_name')} (family overview)",
                content=header + "\n" + first_para,
                chunk_level="family",
                metadata={**meta, "chunk_level": "family"},
            )
        )

    # ---- position chunks --------------------------------------------
    # Any time the description mentions a concrete move in the form "6.Be3"
    # or "…Bg4", split that sentence into its own chunk for fine-grained
    # retrieval.
    sentence_re = re.compile(r"(?<=[.!?])\s+")
    move_re = re.compile(r"(?:\d+\.(?:\.\.)?[A-Za-z0-9+#=\-]+)|(?:…[A-Za-z0-9+#=\-]+)")

    seen_snippets: set[str] = set()
    for sent in sentence_re.split(description):
        sent = sent.strip()
        if len(sent) < 25 or sent in seen_snippets:
            continue
        if move_re.search(sent):
            seen_snippets.add(sent)
            chunks.append(
                Chunk(
                    title=f"{node.get('opening_name')} — move note",
                    content=header + "\n" + sent,
                    chunk_level="position",
                    metadata={**meta, "chunk_level": "position"},
                )
            )
            if len(seen_snippets) >= 4:
                break

    return chunks
