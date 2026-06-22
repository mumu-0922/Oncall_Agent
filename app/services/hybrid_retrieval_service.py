"""Hybrid dense/BM25 retrieval with parent expansion."""

from __future__ import annotations

import hashlib
from pathlib import Path

from langchain_core.documents import Document
from loguru import logger

from app.config import config
from app.models.rag import ChildDocument, RetrievalCandidate
from app.services.bm25_retrieval_service import bm25_retrieval_service
from app.services.rag_document_store import rag_document_store
from app.services.rerank_service import rerank_service
from app.services.vector_store_manager import vector_store_manager


class HybridRetrievalService:
    """Retrieve child chunks from dense + BM25 and expand parent context."""

    def retrieve(
        self,
        query: str,
        mode: str | None = None,
        final_k: int | None = None,
    ) -> list[RetrievalCandidate]:
        retrieval_mode = mode or config.rag_retrieval_mode
        limit = final_k or config.rag_final_top_k
        if retrieval_mode == "dense":
            candidates = self._dense_recall(query, k=limit or config.rag_top_k)
            return self._expand_parent(candidates[:limit]) if config.rag_expand_parent else candidates[:limit]

        dense_candidates = self._dense_recall(query, k=config.rag_dense_fetch_k)
        bm25_candidates = [] if retrieval_mode == "dense" else bm25_retrieval_service.search(query, k=config.rag_bm25_fetch_k)
        merged = self._merge_candidates(dense_candidates, bm25_candidates)
        reranked = rerank_service.rerank(query, merged)
        top_candidates = reranked[:limit]
        if retrieval_mode == "hybrid_parent" and config.rag_expand_parent:
            top_candidates = self._expand_parent(top_candidates)
        logger.info(
            "Hybrid 检索完成 mode={} dense={} bm25={} final={}",
            retrieval_mode,
            len(dense_candidates),
            len(bm25_candidates),
            len(top_candidates),
        )
        return top_candidates

    def _dense_recall(self, query: str, k: int) -> list[RetrievalCandidate]:
        try:
            vector_store = vector_store_manager.get_vector_store()
            docs_and_scores = vector_store.similarity_search_with_score(query, k=k)
        except Exception as exc:
            try:
                logger.warning("dense 检索 with_score 失败，降级 similarity_search: {}", exc)
                vector_store = vector_store_manager.get_vector_store()
                docs_and_scores = [(doc, None) for doc in vector_store.similarity_search(query, k=k)]
            except Exception as fallback_exc:
                logger.warning("dense 检索不可用，返回空 recall: {}", fallback_exc)
                return []

        candidates = []
        for doc, score in docs_and_scores:
            candidates.append(self._candidate_from_document(doc, score))
        return candidates

    def _candidate_from_document(self, doc: Document, dense_score: float | None) -> RetrievalCandidate:
        metadata = getattr(doc, "metadata", {}) or {}
        child_id = metadata.get("child_id") or metadata.get("id") or ""
        parent_id = metadata.get("parent_id") or child_id
        source = metadata.get("_source", "")
        file_name = metadata.get("_file_name") or Path(str(source)).name or "unknown"
        title_path = metadata.get("title_path") or [metadata[key] for key in ("h1", "h2", "h3") if metadata.get(key)]

        if child_id and not rag_document_store.get_child(child_id):
            child = ChildDocument(
                child_id=child_id,
                parent_id=parent_id,
                source=source,
                file_name=file_name,
                title_path=title_path,
                content=doc.page_content,
                content_hash=metadata.get("content_hash", ""),
                chunk_index=metadata.get("chunk_index", 0),
                metadata=metadata,
            )
        else:
            child = rag_document_store.get_child(child_id) if child_id else None

        content = child.content if child else doc.page_content
        fallback_id = child_id or f"dense::{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}"
        parent_id = parent_id or fallback_id
        return RetrievalCandidate(
            child_id=fallback_id,
            parent_id=parent_id,
            content=content,
            source=source,
            file_name=file_name,
            title_path=title_path,
            dense_score=float(dense_score) if dense_score is not None else None,
            retrieval_channels=["dense"],
            metadata=metadata,
        )

    def _merge_candidates(
        self,
        dense_candidates: list[RetrievalCandidate],
        bm25_candidates: list[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        merged: dict[str, RetrievalCandidate] = {}
        for candidate in dense_candidates:
            merged[candidate.child_id] = candidate
        for candidate in bm25_candidates:
            existing = merged.get(candidate.child_id)
            if existing:
                existing.bm25_score = candidate.bm25_score
                existing.add_channel("bm25")
            else:
                merged[candidate.child_id] = candidate
        return list(merged.values())

    def _expand_parent(self, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        expanded = []
        for candidate in candidates:
            parent = rag_document_store.get_parent(candidate.parent_id)
            if not parent:
                expanded.append(candidate)
                continue
            candidate.content = self._parent_window(parent.content, candidate.content)
            candidate.metadata = {
                **candidate.metadata,
                "expanded_parent": True,
                "parent_content_hash": parent.content_hash,
            }
            expanded.append(candidate)
        return expanded

    def _parent_window(self, parent_content: str, child_content: str) -> str:
        max_chars = config.rag_parent_context_max_chars
        if len(parent_content) <= max_chars:
            return parent_content
        needle = child_content[:80]
        index = parent_content.find(needle)
        if index < 0:
            return parent_content[:max_chars]
        half = max_chars // 2
        start = max(0, index - half)
        end = min(len(parent_content), start + max_chars)
        return parent_content[start:end]


hybrid_retrieval_service = HybridRetrievalService()
