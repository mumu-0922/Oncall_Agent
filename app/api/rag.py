"""RAG debugging APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.models.rag import (
    RagRetrieveDebugCandidate,
    RagRetrieveDebugRequest,
    RagRetrieveDebugResponse,
    RetrievalCandidate,
)
from app.services.hybrid_retrieval_service import hybrid_retrieval_service

router = APIRouter()


@router.post("/rag/retrieve_debug", response_model=RagRetrieveDebugResponse)
async def retrieve_debug(request: RagRetrieveDebugRequest) -> RagRetrieveDebugResponse:
    """Return retrieval internals for interview/debug verification.

    This endpoint is intentionally read-only. It exposes retrieval candidates,
    score components, channels, and parent-child ids without invoking the LLM.
    """
    try:
        candidates = hybrid_retrieval_service.retrieve(
            request.query,
            mode=request.mode,
            final_k=request.top_k,
        )
    except Exception as exc:
        logger.exception("RAG retrieve_debug 失败: {}", exc)
        raise HTTPException(status_code=500, detail=f"RAG retrieve_debug failed: {exc}") from exc

    debug_candidates = [_to_debug_candidate(candidate) for candidate in candidates]
    return RagRetrieveDebugResponse(
        query=request.query,
        mode=request.mode,
        top_k=request.top_k,
        candidate_count=len(debug_candidates),
        candidates=debug_candidates,
    )


def _to_debug_candidate(candidate: RetrievalCandidate) -> RagRetrieveDebugCandidate:
    preview = candidate.content.replace("\n", " ").strip()
    if len(preview) > 600:
        preview = preview[:600] + "..."

    return RagRetrieveDebugCandidate(
        child_id=candidate.child_id,
        parent_id=candidate.parent_id,
        source=candidate.source,
        file_name=candidate.file_name,
        title_path=candidate.title_path,
        dense_score=candidate.dense_score,
        bm25_score=candidate.bm25_score,
        rerank_score=candidate.rerank_score,
        retrieval_channels=candidate.retrieval_channels,
        content_preview=preview,
        metadata=candidate.metadata,
    )
