"""RAG chat endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ...rag.chat import answer_question


router = APIRouter()


class ChatRequest(BaseModel):
    user_id: int
    question: str
    fen: str | None = None
    conversation_id: int | None = None
    top_k: int | None = None


class ChatResponse(BaseModel):
    answer: str
    conversation_id: int
    sources: list[dict]


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
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
