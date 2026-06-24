import sys
from pathlib import Path

from scripts.eval_aiops_agent import (
    evaluate,
    expected_tool_hit_rate,
    load_aiops_cases,
    response_has_unsupported_root_cause,
    tool_call_success_rate,
)


def test_expected_tool_hit_rate_handles_empty_expectations():
    assert expected_tool_hit_rate([], []) == 1.0
    assert expected_tool_hit_rate(["search_local_logs"], []) == 1.0


def test_expected_tool_hit_rate_scores_partial_hit():
    assert expected_tool_hit_rate(["query_cpu_metrics"], ["query_cpu_metrics", "query_memory_metrics"]) == 0.5


def test_tool_call_success_rate_detects_payload_error():
    events = [
        {
            "kind": "tool_result",
            "tool": "search_local_logs",
            "status": "completed",
            "summary": '{"error": "AIOPS_SERVICE_LOG_MAP 为空"}',
        },
        {
            "kind": "tool_result",
            "tool": "query_cpu_metrics",
            "status": "completed",
            "summary": "{}",
        },
    ]

    assert tool_call_success_rate(events) == 0.5


def test_unsupported_root_cause_requires_actionable_evidence():
    assert response_has_unsupported_root_cause("根因是内存泄漏。", actionable_evidence_count=0) is True
    assert response_has_unsupported_root_cause("证据不足，不能下根因结论。", actionable_evidence_count=0) is False
    assert response_has_unsupported_root_cause("根因是 CPU 饱和。", actionable_evidence_count=1) is False


def test_aiops_eval_cases_generate_required_summary_metrics():
    cases = load_aiops_cases(Path("evals/aiops_cases.json"))
    report = evaluate(cases)
    summary = report["summary"]

    assert report["mode"] == "offline_trace"
    assert report["case_count"] >= 6
    for metric in (
        "tool_call_success_rate",
        "expected_tool_hit_rate",
        "evidence_coverage",
        "hallucination_block_rate",
        "insufficient_evidence_rate",
        "avg_latency_ms",
        "timeout_rate",
    ):
        assert metric in summary
    assert summary["expected_tool_hit_rate"] == 1.0
    assert summary["hallucination_block_rate"] == 1.0
    assert summary["timeout_rate"] > 0
    assert summary["insufficient_evidence_rate"] > 0


def test_aiops_eval_includes_missing_log_source_case_without_fake_data():
    cases = load_aiops_cases(Path("evals/aiops_cases.json"))
    report = evaluate(cases)
    rows = {row["id"]: row for row in report["cases"]}

    row = rows["log_source_missing_insufficient_evidence"]
    assert row["actual_outcome"] == "insufficient_evidence"
    assert row["tool_call_success_rate"] == 0.0
    assert row["expected_error_substrings_present"] == 1
    assert row["hallucination_blocked"] == 1


def test_aiops_eval_detects_timeout_case():
    cases = load_aiops_cases(Path("evals/aiops_cases.json"))
    report = evaluate(cases)
    rows = {row["id"]: row for row in report["cases"]}

    row = rows["metric_query_timeout_accounted"]
    assert row["actual_outcome"] == "timeout"
    assert row["timeout"] == 1
    assert row["tool_error"] == 1


def test_importing_analyzers_does_not_eager_load_planner_or_replanner():
    # Offline evals import deterministic analyzers via EvidencePackageService.
    # That path must not initialize planner/replanner/RAG side effects.
    for module_name in (
        "app.agent.aiops.planner",
        "app.agent.aiops.replanner",
        "app.services.vector_embedding_service",
    ):
        sys.modules.pop(module_name, None)

    import app.agent.aiops.analyzers.rules  # noqa: F401

    assert "app.agent.aiops.planner" not in sys.modules
    assert "app.agent.aiops.replanner" not in sys.modules
    assert "app.services.vector_embedding_service" not in sys.modules
