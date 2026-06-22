from scripts.eval_retrieval import retrieval_metrics


def test_retrieval_metrics_hit_and_mrr():
    metrics = retrieval_metrics(
        retrieved_sources=["a.md", "b.md", "c.md"],
        expected_sources=["b.md"],
        k=3,
    )

    assert metrics["hit"] == 1
    assert metrics["recall_at_k"] == 1.0
    assert metrics["precision_at_k"] == 1 / 3
    assert metrics["mrr"] == 0.5
    assert metrics["first_hit_rank"] == 2


def test_retrieval_metrics_miss():
    metrics = retrieval_metrics(
        retrieved_sources=["a.md", "c.md"],
        expected_sources=["b.md"],
        k=3,
    )

    assert metrics["hit"] == 0
    assert metrics["recall_at_k"] == 0.0
    assert metrics["mrr"] == 0.0
