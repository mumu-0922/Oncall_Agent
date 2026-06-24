import json

import pytest

from app.agent.aiops.analyzers.rules import run_analyzers
from app.models.evidence import EvidenceItem
from app.services.evidence_package_service import EvidencePackageService

replanner_module = __import__("app.agent.aiops.replanner", fromlist=["*"])


def _metric_item(metric_name: str, value: float, *, item_id: str = "E001-metric") -> EvidenceItem:
    payload = {
        "service_name": "super-biz-agent",
        "metric_name": metric_name,
        "source": "local_wsl:/proc",
        "history_available": False,
        "data_points": [{"timestamp": "11:00:00", "value": value}],
        "statistics": {"avg": value, "max": value, "min": value, "p95": value},
        "alert_info": {"triggered": value >= 80, "threshold": 80},
    }
    return EvidenceItem(
        id=item_id,
        kind="metric",
        source="local_wsl:/proc",
        tool_name="query_cpu_metrics" if "cpu" in metric_name else "query_memory_metrics",
        status="completed",
        summary=f"{metric_name}={value}",
        raw_excerpt=json.dumps(payload, ensure_ascii=False),
        metadata={
            "service_name": "super-biz-agent",
            "metric_name": metric_name,
            "source": "local_wsl:/proc",
            "history_available": False,
            "statistics": payload["statistics"],
            "alert_info": payload["alert_info"],
        },
    )


def test_cpu_analyzer_marks_critical_high_cpu():
    findings = run_analyzers([_metric_item("cpu_usage_percent", 96.0)])

    cpu = next(finding for finding in findings if finding.analyzer == "cpu_high")
    assert cpu.id == "F001-cpu_high"
    assert cpu.status == "critical"
    assert cpu.severity == "critical"
    assert cpu.evidence_refs == ["E001-metric"]
    assert "search_log" in cpu.next_queries


def test_memory_analyzer_marks_normal_memory():
    findings = run_analyzers([_metric_item("memory_usage_percent", 22.0)])

    memory = next(finding for finding in findings if finding.analyzer == "memory_high")
    assert memory.status == "normal"
    assert memory.severity == "info"
    assert memory.next_queries == []


def test_log_error_spike_analyzer_counts_error_logs():
    logs = [
        {"level": "ERROR", "message": "database timeout"},
        {"level": "INFO", "message": "ok"},
        {"level": "WARN", "message": "exception in worker"},
    ]
    payload = {
        "topic_id": "local:super-biz-agent",
        "source": "local_wsl:file",
        "total": 3,
        "logs": logs,
    }
    item = EvidenceItem(
        id="E002-log",
        kind="log",
        source="local_wsl:file",
        tool_name="search_log",
        status="completed",
        summary="3 logs",
        raw_excerpt=json.dumps(payload, ensure_ascii=False),
        metadata={"topic_id": "local:super-biz-agent", "source": "local_wsl:file", "total": 3},
    )

    findings = run_analyzers([item])
    log_finding = next(finding for finding in findings if finding.analyzer == "log_error_spike")
    assert log_finding.status == "warning"
    assert log_finding.metadata["error_count"] == 2
    assert log_finding.evidence_refs == ["E002-log"]


def test_evidence_package_includes_analyzer_findings_and_confidence():
    payload = {
        "service_name": "super-biz-agent",
        "metric_name": "cpu_usage_percent",
        "source": "local_wsl:/proc",
        "history_available": False,
        "data_points": [{"timestamp": "11:00:00", "value": 96.0}],
        "statistics": {"avg": 91.0, "max": 96.0, "min": 88.0, "p95": 96.0},
        "alert_info": {"triggered": True, "threshold": 80},
    }
    package = EvidencePackageService().build_from_state(
        {
            "input": "诊断 CPU 告警",
            "past_steps": [],
            "tool_events": [
                {
                    "kind": "tool_result",
                    "tool": "query_cpu_metrics",
                    "status": "completed",
                    "summary": json.dumps(payload, ensure_ascii=False),
                }
            ],
        }
    )

    assert package.confidence == "high"
    assert package.findings
    assert package.findings[0].analyzer == "cpu_high"
    assert package.findings[0].status == "critical"
    prompt = package.to_prompt_markdown()
    assert "Analyzer Findings" in prompt
    assert "F001-cpu_high" in prompt


@pytest.mark.asyncio
async def test_replanner_evidence_index_includes_findings(monkeypatch):
    class DummyPrompt:
        def __or__(self, other):
            return other

    class Chain:
        def __init__(self, schema):
            self.schema = schema
            self.payload = None

        async def ainvoke(self, payload):
            self.payload = payload
            return self.schema(response="# 报告\n\nCPU 告警存在。")

    class Model:
        def __init__(self):
            self.chain = None

        def with_structured_output(self, schema, method):
            self.chain = Chain(schema)
            return self.chain

    payload = {
        "service_name": "super-biz-agent",
        "metric_name": "cpu_usage_percent",
        "source": "local_wsl:/proc",
        "history_available": False,
        "data_points": [{"timestamp": "11:00:00", "value": 96.0}],
        "statistics": {"max": 96.0},
        "alert_info": {"triggered": True, "threshold": 80},
    }
    model = Model()
    monkeypatch.setattr(replanner_module, "response_prompt", DummyPrompt())
    monkeypatch.setattr(replanner_module.config, "aiops_structured_output_method", "function_calling")
    monkeypatch.setattr(replanner_module.config, "llm_timeout_seconds", 1)

    result = await replanner_module._generate_response(
        {
            "input": "诊断 CPU 告警",
            "plan": [],
            "past_steps": [("查询 CPU", json.dumps(payload, ensure_ascii=False))],
            "tool_events": [],
            "response": "",
        },
        model,
    )

    assert "Analyzer Findings" in result["response"]
    assert "F001-cpu_high" in result["response"]
    assert result["evidence_package"]["findings"][0]["status"] == "critical"
    assert "Analyzer Findings" in model.chain.payload["messages"][2][1]
