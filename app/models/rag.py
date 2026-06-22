"""RAG parent-child retrieval models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ParentDocument(BaseModel):
    """A large, semantically complete context unit."""

    parent_id: str = Field(..., description="Stable parent chunk id")
    source: str = Field(..., description="Original source file path")
    file_name: str = Field(..., description="Original source file name")
    title_path: list[str] = Field(default_factory=list, description="Markdown title hierarchy")
    content: str = Field(..., description="Parent chunk content")
    content_hash: str = Field(..., description="SHA256 hash of parent content")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ChildDocument(BaseModel):
    """A small retrieval unit pointing back to its parent."""

    child_id: str = Field(..., description="Stable child chunk id")
    parent_id: str = Field(..., description="Parent chunk id")
    source: str = Field(..., description="Original source file path")
    file_name: str = Field(..., description="Original source file name")
    title_path: list[str] = Field(default_factory=list, description="Markdown title hierarchy")
    content: str = Field(..., description="Child chunk content")
    content_hash: str = Field(..., description="SHA256 hash of child content")
    chunk_index: int = Field(..., ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class RetrievalCandidate(BaseModel):
    """Candidate returned by dense/BM25/hybrid retrieval."""

    child_id: str
    parent_id: str
    content: str
    source: str
    file_name: str
    title_path: list[str] = Field(default_factory=list)
    dense_score: float | None = None
    bm25_score: float | None = None
    rerank_score: float | None = None
    retrieval_channels: list[Literal["dense", "bm25"]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def add_channel(self, channel: Literal["dense", "bm25"]) -> None:
        """Append a retrieval channel once."""
        if channel not in self.retrieval_channels:
            self.retrieval_channels.append(channel)


RetrievalMode = Literal["dense", "hybrid", "hybrid_parent"]


class RagRetrieveDebugRequest(BaseModel):
    """Request body for retrieval debugging."""

    query: str = Field(..., min_length=1, description="User query to retrieve against")
    mode: RetrievalMode = Field(default="hybrid_parent", description="Retrieval mode")
    top_k: int = Field(default=3, ge=1, le=20, description="Final candidate count")


class RagRetrieveDebugCandidate(BaseModel):
    """Debug view of one retrieval candidate."""

    child_id: str
    parent_id: str
    source: str
    file_name: str
    title_path: list[str] = Field(default_factory=list)
    dense_score: float | None = None
    bm25_score: float | None = None
    rerank_score: float | None = None
    retrieval_channels: list[Literal["dense", "bm25"]] = Field(default_factory=list)
    content_preview: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagRetrieveDebugResponse(BaseModel):
    """Response body for retrieval debugging."""

    query: str
    mode: RetrievalMode
    top_k: int
    candidate_count: int
    candidates: list[RagRetrieveDebugCandidate]
