import pytest

from app.api.rag import retrieve_debug
from app.models.rag import RagRetrieveDebugRequest, RetrievalCandidate


@pytest.mark.asyncio
async def test_retrieve_debug_returns_score_channels_and_parent_ids(monkeypatch: pytest.MonkeyPatch):
    def fake_retrieve(query: str, mode: str, final_k: int):
        assert query == "CPU 使用率高怎么排查"
        assert mode == "hybrid_parent"
        assert final_k == 1
        return [
            RetrievalCandidate(
                child_id="child-1",
                parent_id="parent-1",
                content="CPU 使用率过高排查步骤\n" * 80,
                source="/tmp/cpu_high_usage.md",
                file_name="cpu_high_usage.md",
                title_path=["CPU 使用率过高", "排查步骤"],
                dense_score=0.21,
                bm25_score=6.5,
                rerank_score=0.91,
                retrieval_channels=["dense", "bm25"],
                metadata={"expanded_parent": True},
            )
        ]

    monkeypatch.setattr("app.api.rag.hybrid_retrieval_service.retrieve", fake_retrieve)

    response = await retrieve_debug(
        RagRetrieveDebugRequest(
            query="CPU 使用率高怎么排查",
            mode="hybrid_parent",
            top_k=1,
        )
    )

    assert response.query == "CPU 使用率高怎么排查"
    assert response.mode == "hybrid_parent"
    assert response.candidate_count == 1
    candidate = response.candidates[0]
    assert candidate.child_id == "child-1"
    assert candidate.parent_id == "parent-1"
    assert candidate.file_name == "cpu_high_usage.md"
    assert candidate.dense_score == 0.21
    assert candidate.bm25_score == 6.5
    assert candidate.rerank_score == 0.91
    assert set(candidate.retrieval_channels) == {"dense", "bm25"}
    assert len(candidate.content_preview) <= 603
    assert candidate.metadata["expanded_parent"] is True
