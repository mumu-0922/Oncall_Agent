"""Lightweight BM25 retrieval over child chunks."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from loguru import logger

from app.config import config
from app.models.rag import ChildDocument, RetrievalCandidate
from app.services.rag_document_store import rag_document_store

CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def tokenize_for_bm25(text: str) -> list[str]:
    """Tokenize Chinese and English text for BM25.

    Keeps English/service tokens, Chinese chars, and Chinese bigrams so both
    exact terms (HighCPUUsage) and Chinese phrases (磁盘/内存) can match.
    """
    lowered = text.lower()
    tokens = [match.group(0) for match in WORD_RE.finditer(lowered)]
    chinese_chars = "".join(CHINESE_RE.findall(lowered))
    tokens.extend(chinese_chars[i : i + 2] for i in range(max(0, len(chinese_chars) - 1)))
    return [token for token in tokens if token.strip()]


@dataclass
class _IndexedChild:
    child: ChildDocument
    term_freq: Counter[str]
    doc_len: int


class BM25RetrievalService:
    """In-process BM25 index for docstore child chunks."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._indexed: list[_IndexedChild] = []
        self._idf: dict[str, float] = {}
        self._avg_doc_len = 0.0
        self._loaded_child_count = 0
        self._external_index = False

    def rebuild_index(self, children: Iterable[ChildDocument] | None = None) -> None:
        """Rebuild BM25 index from children or docstore."""
        self._external_index = children is not None
        child_list = list(children) if children is not None else rag_document_store.list_children()
        indexed: list[_IndexedChild] = []
        document_frequency: Counter[str] = Counter()

        for child in child_list:
            full_text = " ".join([child.file_name, *child.title_path, child.content])
            tokens = tokenize_for_bm25(full_text)
            term_freq = Counter(tokens)
            indexed_child = _IndexedChild(child=child, term_freq=term_freq, doc_len=len(tokens))
            indexed.append(indexed_child)
            document_frequency.update(term_freq.keys())

        total_docs = len(indexed)
        self._indexed = indexed
        self._avg_doc_len = sum(item.doc_len for item in indexed) / total_docs if total_docs else 0.0
        self._idf = {
            term: math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }
        self._loaded_child_count = total_docs
        logger.info("BM25 索引重建完成 children={}, vocab={}", total_docs, len(self._idf))

    def ensure_index_loaded(self) -> None:
        """Lazy-load index from docstore."""
        if self._external_index:
            return
        current_count = len(rag_document_store.list_children())
        if not self._indexed or current_count != self._loaded_child_count:
            self.rebuild_index()

    def search(self, query: str, k: int | None = None) -> list[RetrievalCandidate]:
        """Search child chunks with BM25."""
        self.ensure_index_loaded()
        limit = k or config.rag_bm25_fetch_k
        query_terms = tokenize_for_bm25(query)
        if not query_terms or not self._indexed:
            return []

        scored: list[tuple[float, ChildDocument]] = []
        for item in self._indexed:
            score = self._score(query_terms, item)
            if score > 0:
                scored.append((score, item.child))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        candidates = []
        for score, child in scored[:limit]:
            candidates.append(
                RetrievalCandidate(
                    child_id=child.child_id,
                    parent_id=child.parent_id,
                    content=child.content,
                    source=child.source,
                    file_name=child.file_name,
                    title_path=child.title_path,
                    bm25_score=score,
                    retrieval_channels=["bm25"],
                    metadata=child.metadata,
                )
            )
        logger.debug("BM25 检索完成 query='{}' results={}", query, len(candidates))
        return candidates

    def _score(self, query_terms: list[str], item: _IndexedChild) -> float:
        score = 0.0
        avg_doc_len = self._avg_doc_len or 1.0
        for term in query_terms:
            tf = item.term_freq.get(term, 0)
            if tf <= 0:
                continue
            idf = self._idf.get(term, 0.0)
            denominator = tf + self.k1 * (1 - self.b + self.b * item.doc_len / avg_doc_len)
            score += idf * (tf * (self.k1 + 1)) / denominator
        return score


bm25_retrieval_service = BM25RetrievalService()
