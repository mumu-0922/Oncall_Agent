from app.models.rag import ParentDocument, RetrievalCandidate
from app.services.hybrid_retrieval_service import HybridRetrievalService
from app.services.rerank_service import RerankService


class DummyStore:
    def __init__(self, parent):
        self.parent = parent

    def get_parent(self, parent_id):
        return self.parent if parent_id == self.parent.parent_id else None

    def get_child(self, child_id):
        return None


def candidate(child_id, parent_id, content, dense=None, bm25=None, channels=None):
    return RetrievalCandidate(
        child_id=child_id,
        parent_id=parent_id,
        content=content,
        source=f"/tmp/{child_id}.md",
        file_name=f"{child_id}.md",
        title_path=[child_id],
        dense_score=dense,
        bm25_score=bm25,
        retrieval_channels=channels or [],
        metadata={},
    )


def test_merge_candidates_deduplicates_and_combines_channels():
    service = HybridRetrievalService()
    dense = [candidate("c1", "p1", "CPU", dense=0.2, channels=["dense"])]
    bm25 = [candidate("c1", "p1", "CPU", bm25=3.0, channels=["bm25"])]

    merged = service._merge_candidates(dense, bm25)

    assert len(merged) == 1
    assert merged[0].dense_score == 0.2
    assert merged[0].bm25_score == 3.0
    assert set(merged[0].retrieval_channels) == {"dense", "bm25"}


def test_rerank_prefers_query_keyword_and_dual_channel():
    reranker = RerankService()
    candidates = [
        candidate("cpu", "p1", "HighCPUUsage CPU 使用率过高", dense=0.1, bm25=4.0, channels=["dense", "bm25"]),
        candidate("disk", "p2", "磁盘 使用率过高", dense=0.2, bm25=1.0, channels=["bm25"]),
    ]

    ranked = reranker.rerank("CPU HighCPUUsage", candidates)

    assert ranked[0].child_id == "cpu"
    assert ranked[0].rerank_score is not None


def test_expand_parent_replaces_child_with_parent_context(monkeypatch):
    parent = ParentDocument(
        parent_id="p1",
        source="/tmp/cpu.md",
        file_name="cpu.md",
        title_path=["CPU"],
        content="完整父块上下文：HighCPUUsage CPU 使用率过高，需要查询日志和监控。",
        content_hash="hash",
        metadata={},
    )
    service = HybridRetrievalService()
    monkeypatch.setattr("app.services.hybrid_retrieval_service.rag_document_store", DummyStore(parent))
    candidates = [candidate("c1", "p1", "HighCPUUsage CPU 使用率过高", channels=["bm25"])]

    expanded = service._expand_parent(candidates)

    assert "完整父块上下文" in expanded[0].content
    assert expanded[0].metadata["expanded_parent"] is True
