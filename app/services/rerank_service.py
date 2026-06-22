"""Rule-based reranking and score fusion for hybrid RAG."""

from __future__ import annotations

from app.config import config
from app.models.rag import RetrievalCandidate
from app.services.bm25_retrieval_service import tokenize_for_bm25


def _normalize(values: list[float | None], reverse: bool = False) -> list[float]:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return [0.0 for _ in values]
    min_value = min(numeric)
    max_value = max(numeric)
    if max_value == min_value:
        return [1.0 if value is not None else 0.0 for value in values]
    normalized = []
    for value in values:
        if value is None:
            normalized.append(0.0)
        elif reverse:
            normalized.append((max_value - value) / (max_value - min_value))
        else:
            normalized.append((value - min_value) / (max_value - min_value))
    return normalized


class RerankService:
    """Score fusion reranker for dense + BM25 candidates."""

    def rerank(self, query: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        dense_norm = _normalize([item.dense_score for item in candidates], reverse=True)
        bm25_norm = _normalize([item.bm25_score for item in candidates])
        query_terms = set(tokenize_for_bm25(query))

        reranked = []
        for index, candidate in enumerate(candidates):
            exact_boost = self._exact_match_boost(query_terms, candidate)
            channel_boost = 0.05 if len(candidate.retrieval_channels) > 1 else 0.0
            score = (
                config.rag_dense_weight * dense_norm[index]
                + config.rag_bm25_weight * bm25_norm[index]
                + exact_boost
                + channel_boost
            )
            candidate.rerank_score = round(score, 6)
            reranked.append(candidate)
        reranked.sort(key=lambda item: item.rerank_score or 0.0, reverse=True)
        return reranked

    def _exact_match_boost(self, query_terms: set[str], candidate: RetrievalCandidate) -> float:
        haystack = " ".join([candidate.file_name, *candidate.title_path, candidate.content])
        candidate_terms = set(tokenize_for_bm25(haystack))
        if not query_terms:
            return 0.0
        overlap_ratio = len(query_terms & candidate_terms) / len(query_terms)
        title_terms = set(tokenize_for_bm25(" ".join([candidate.file_name, *candidate.title_path])))
        title_overlap = len(query_terms & title_terms) / len(query_terms)
        return min(0.25, overlap_ratio * 0.15 + title_overlap * 0.10)


rerank_service = RerankService()
