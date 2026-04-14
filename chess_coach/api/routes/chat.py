"""RAG chat endpoints.

Two paths share this route:

* **Legacy RAG**: hybrid retrieve → prompt → answer (``use_agent=False``,
  default).
* **Tool-calling agent**: the multi-tool coach from ``chess_coach.agents``.
  Supports persistent conversation memory (for the coach widget) plus a
  ``page_context`` payload so the coach can ground "why did I blunder?"
  style questions against the user's current page / position / card.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ...agents import coach_answer
from ...rag.chat import answer_question


router = APIRouter()


class ChatRequest(BaseModel):
    user_id: int
    question: str
    fen: str | None = None
    conversation_id: int | None = None
    top_k: int | None = None
    use_agent: bool = False
    page_context: dict | None = None


class ChatResponse(BaseModel):
    answer: str
    conversation_id: int
    sources: list[dict]
    tool_trace: list[dict] | None = None


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if req.use_agent:
        answer_text, trace, conv_id = await coach_answer(
            user_id=req.user_id,
            question=req.question,
            fen=req.fen,
            conversation_id=req.conversation_id,
            page_context=req.page_context,
        )
        return ChatResponse(
            answer=answer_text,
            conversation_id=conv_id or 0,
            sources=[],
            tool_trace=[tc.to_dict() for tc in trace] if trace else None,
        )

    answer, conv_id, results = await answer_question(
        user_id=req.user_id,
        question=req.question,
        fen=req.fen,
        conversation_id=req.conversation_id,
        top_k=req.top_k,
    )
    return ChatResponse(
        answer=answer,
        conversation_id=conv_id,
        sources=[
            {
                "doc_id": r.doc_id,
                "title": r.title,
                "chunk_level": r.chunk_level,
                "opening_node_id": r.opening_node_id,
                "score": r.score,
                "metadata": r.metadata,
            }
            for r in results
        ],
    )
