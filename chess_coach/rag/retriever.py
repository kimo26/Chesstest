"""Hybrid BM25 + dense retrieval with RRF fusion, implemented entirely in
PostgreSQL.

The dense search uses pgvector's ``<=>`` cosine distance operator on the
HNSW index. The BM25 search uses the builtin tsvector + ``ts_rank_cd`` scoring
function. RRF fusion gives us a single ranking without having to normalise
the two score scales.

If a ``fen_canonical`` is supplied we also fetch the exact node, its parent,
and its immediate children so the caller can send the LLM a full hierarchical
context rather than just the top-k hits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .. import db
from ..config import settings
from ..llm import get_client


@dataclass
class RagResult:
    doc_id: int
    title: str
    content: str
    chunk_level: str
    opening_node_id: int | None
    score: float
    metadata: dict[str, Any]


async def hybrid_search(
    query: str,
    *,
    top_k: int | None = None,
    filter_eco: str | None = None,
    filter_opening_node_id: int | None = None,
    fen_context: str | None = None,
) -> list[RagResult]:
    """Run BM25 + dense search in one SQL query, fuse with RRF."""
    top_k = top_k or settings.rag_final_top_k
    client = get_client()
    embedding = await client.embed_one(query)
    evec = db.vector_literal(embedding)

    filters_sql: list[str] = []
    params: list[Any] = [
        evec,                            # $1: dense query vector
        query,                           # $2: fts query text
        settings.rag_rrf_k,              # $3: RRF constant
        settings.rag_top_k_dense,        # $4
        settings.rag_top_k_bm25,         # $5
        top_k,                           # $6
    ]
    if filter_eco:
        params.append(filter_eco)
        filters_sql.append(f"AND (metadata->>'eco_code') = ${len(params)}")
    if filter_opening_node_id is not None:
        params.append(filter_opening_node_id)
        filters_sql.append(f"AND opening_node_id = ${len(params)}")
    where = " ".join(filters_sql)

    sql = f"""
    WITH params AS (
        SELECT $1::vector AS qvec, $2::text AS qtext, $3::int AS k,
               $4::int AS topk_dense, $5::int AS topk_bm25, $6::int AS final_k
    ),
    semantic AS (
        SELECT d.id,
               1.0 / (p.k + ROW_NUMBER() OVER (ORDER BY d.embedding <=> p.qvec)) AS rrf
        FROM rag_documents d, params p
        WHERE d.embedding IS NOT NULL {where}
        ORDER BY d.embedding <=> p.qvec
        LIMIT (SELECT topk_dense FROM params)
    ),
    keyword AS (
        SELECT d.id,
               1.0 / (p.k + ROW_NUMBER() OVER (
                   ORDER BY ts_rank_cd(d.content_tsvector, plainto_tsquery('english', p.qtext)) DESC
               )) AS rrf
        FROM rag_documents d, params p
        WHERE d.content_tsvector @@ plainto_tsquery('english', p.qtext) {where}
        LIMIT (SELECT topk_bm25 FROM params)
    ),
    combined AS (
        SELECT COALESCE(s.id, k.id) AS id,
               COALESCE(s.rrf, 0) + COALESCE(k.rrf, 0) AS score
        FROM semantic s
        FULL OUTER JOIN keyword k ON s.id = k.id
    )
    SELECT d.id, d.title, d.content, d.chunk_level, d.opening_node_id,
           d.metadata, c.score
    FROM combined c
    JOIN rag_documents d ON d.id = c.id
    ORDER BY c.score DESC
    LIMIT (SELECT final_k FROM params)
    """
    rows = await db.fetch(sql, *params)

    results = [
        RagResult(
            doc_id=int(r["id"]),
            title=r["title"],
            content=r["content"],
            chunk_level=r["chunk_level"],
            opening_node_id=r["opening_node_id"],
            score=float(r["score"]),
            metadata=dict(r["metadata"] or {}),
        )
        for r in rows
    ]

    # --- graph expansion ---------------------------------------------
    # Always include the parent and immediate children of the top hit so
    # the LLM gets "where does this come from" / "where does this go".
    if results:
        top = results[0]
        if top.opening_node_id:
            extra = await _fetch_tree_neighbours(top.opening_node_id)
            results.extend(extra)

    # If we were given the current board's FEN, add its exact opening node
    # chunk on top so the model always sees "the position you are asking
    # about" first.
    if fen_context:
        canon = " ".join(fen_context.split()[:4])
        fen_chunks = await db.fetch(
            """
            SELECT d.id, d.title, d.content, d.chunk_level, d.opening_node_id,
                   d.metadata
            FROM rag_documents d
            JOIN opening_nodes o ON o.id = d.opening_node_id
            WHERE o.fen_canonical = $1
            ORDER BY d.chunk_level = 'variation' DESC
            LIMIT 2
            """,
            canon,
        )
        fen_results = [
            RagResult(
                doc_id=int(r["id"]),
                title=r["title"],
                content=r["content"],
                chunk_level=r["chunk_level"],
                opening_node_id=r["opening_node_id"],
                score=999.0,
                metadata=dict(r["metadata"] or {}),
            )
            for r in fen_chunks
        ]
        results = _dedupe(fen_results + results)

    return results[:top_k + 4]  # let a little headroom through for context


async def _fetch_tree_neighbours(node_id: int) -> list[RagResult]:
    rows = await db.fetch(
        """
        WITH target AS (
            SELECT id, parent_id FROM opening_nodes WHERE id = $1
        ),
        neighbours AS (
            SELECT o.id
            FROM opening_nodes o, target t
            WHERE o.id = t.parent_id
               OR o.parent_id = t.id
            LIMIT 6
        )
        SELECT d.id, d.title, d.content, d.chunk_level, d.opening_node_id,
               d.metadata
        FROM rag_documents d
        JOIN neighbours n ON n.id = d.opening_node_id
        WHERE d.chunk_level = 'variation'
        """,
        node_id,
    )
    return [
        RagResult(
            doc_id=int(r["id"]),
            title=r["title"],
            content=r["content"],
            chunk_level=r["chunk_level"],
            opening_node_id=r["opening_node_id"],
            score=0.0,
            metadata=dict(r["metadata"] or {}),
        )
        for r in rows
    ]


def _dedupe(results: Sequence[RagResult]) -> list[RagResult]:
    seen: set[int] = set()
    out: list[RagResult] = []
    for r in results:
        if r.doc_id in seen:
            continue
        seen.add(r.doc_id)
        out.append(r)
    return out
