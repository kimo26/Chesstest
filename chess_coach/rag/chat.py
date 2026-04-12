"""RAG chat: retrieve → prompt → (optionally) stream.

This is the function the API layer calls when a user asks a coaching
question. Also persists the message pair to ``rag_conversations`` /
``rag_messages``.
"""
from __future__ import annotations

from typing import AsyncIterator, Sequence

from .. import db
from ..llm import get_client
from ..llm.prompts import PROMPTS
from ..config import settings
from .retriever import RagResult, hybrid_search


def _format_context(results: Sequence[RagResult]) -> str:
    parts: list[str] = []
    for i, r in enumerate(results, 1):
        parts.append(f"[#{i}] ({r.chunk_level}) {r.title}\n{r.content}")
    return "\n\n".join(parts)


async def _save_conversation(
    user_id: int,
    conversation_id: int | None,
    fen: str | None,
    question: str,
    answer: str,
    used_doc_ids: list[int],
) -> int:
    if conversation_id is None:
        row = await db.fetchrow(
            """
            INSERT INTO rag_conversations (user_id, title, context_fen)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            user_id,
            question[:80],
            fen,
        )
        conversation_id = int(row["id"])

    await db.execute(
        """
        INSERT INTO rag_messages (conversation_id, role, content, retrieved_doc_ids, model_used)
        VALUES ($1,'user',$2,$3,$4), ($1,'assistant',$5,$3,$4)
        """,
        conversation_id,
        question,
        used_doc_ids,
        settings.ollama_gen_model,
        answer,
    )
    return conversation_id


async def answer_question(
    user_id: int,
    question: str,
    *,
    fen: str | None = None,
    conversation_id: int | None = None,
    top_k: int | None = None,
) -> tuple[str, int, list[RagResult]]:
    results = await hybrid_search(question, top_k=top_k, fen_context=fen)
    context = _format_context(results)

    prompt = PROMPTS["rag_chat"]
    user_text = prompt.user.format(
        fen=fen or "—",
        context=context or "(no relevant passages retrieved)",
        question=question,
    )

    client = get_client()
    answer = await client.chat(
        [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user_text},
        ],
        temperature=0.2,
    )

    conv_id = await _save_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
        fen=fen,
        question=question,
        answer=answer,
        used_doc_ids=[r.doc_id for r in results],
    )
    return answer, conv_id, results


async def stream_answer(
    user_id: int,
    question: str,
    *,
    fen: str | None = None,
    conversation_id: int | None = None,
) -> AsyncIterator[str]:
    """Streaming variant used by the WebSocket / SSE endpoints."""
    results = await hybrid_search(question, fen_context=fen)
    context = _format_context(results)
    prompt = PROMPTS["rag_chat"]
    user_text = prompt.user.format(
        fen=fen or "—",
        context=context or "(no relevant passages retrieved)",
        question=question,
    )
    client = get_client()
    chunks: list[str] = []
    async for piece in client.chat_stream(
        [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user_text},
        ],
        temperature=0.2,
    ):
        chunks.append(piece)
        yield piece

    await _save_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
        fen=fen,
        question=question,
        answer="".join(chunks),
        used_doc_ids=[r.doc_id for r in results],
    )
