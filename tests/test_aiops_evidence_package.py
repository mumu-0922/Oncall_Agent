import json

import pytest

from app.agent.aiops import __all__ as aiops_exports, replanner as exported_replanner
from app.models.evidence import EvidencePackage
from app.services.evidence_package_service import EvidencePackageService

replanner_module = __import__("app.agent.aiops.replanner", fromlist=["*"])


def _metric_payload(**overrides):
    payload = {
        "service_name": "super-biz-agent",
        "metric_name": "cpu_usage_percent",
        "source": "local_wsl:/proc",
        "history_available": False,
        "data_points": [{"timestamp": "11:00:00", "value": 12.3, "scope": "process"}],
        "statistics": {"avg": 12.3, "max": 12.3, "min": 12.3},
        "alert_info": {"triggered": False, "threshold": 80, "message": "CPU 正常"},
    }
    payload.update(overrides)
    return payload


def test_evidence_package_builds_metric_from_tool_trace():
    service = EvidencePackageService()
    payload = _metric_payload()
    package = service.build_from_state(
        {
            "input": "诊断当前系统",
            "past_steps": [],
            "tool_events": [
                {
                    "kind": "tool_result",
                    "tool": "query_cpu_metrics",
                    "status": "completed",
                    "summary": json.dumps(payload, ensure_ascii=False),
                }
            ],
            "response": "",
        }
    )

    assert isinstance(package, EvidencePackage)
    assert package.has_actionable_evidence is True
    assert package.actionable_evidence_count == 1
    assert package.confidence == "medium"
    assert package.service_names == ["super-biz-agent"]
    assert package.metrics[0].kind == "metric"
    assert package.metrics[0].source == "local_wsl:/proc"
    assert "history_available=false" in package.limitations[0]


def test_evidence_package_keeps_tool_error_as_first_class_evidence():
    service = EvidencePackageService()
    package = service.build_from_state(
        {
            "input": "诊断当前系统",
            "past_steps": [],
            "tool_events": [
                {
                    "kind": "tool_result",
                    "tool": "query_cpu_metrics",
                    "status": "completed",
                    "summary": json.dumps(
                        {
                            "service_name": "super-biz-agent",
                            "metric_name": "cpu_usage_percent",
                            "source": "disabled",
                            "error": "未配置可用监控数据源: AIOPS_MONITOR_PROVIDER=disabled",
                            "suggestion": "设置 AIOPS_MONITOR_PROVIDER=local_wsl/local_vps",
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            "response": "",
        }
    )

    assert package.has_actionable_evidence is False
    assert package.tool_errors
    assert "未配置可用监控数据源" in package.tool_errors[0].summary

    report = service.render_insufficient_evidence_report(package)
    assert "证据不足" in report
    assert "未配置可用监控数据源" in report
    assert "下一步需要补齐" in report


@pytest.mark.asyncio
async def test_replanner_returns_insufficient_evidence_without_calling_llm(monkeypatch):
    class ExplodingModel:
        def with_structured_output(self, *args, **kwargs):
            raise AssertionError("LLM should not be called when evidence is not actionable")

    result = await replanner_module._generate_response(
        {
            "input": "诊断告警",
            "plan": [],
            "past_steps": [("只查知识库", "工具证据：retrieve_knowledge 返回 CPU 排查手册")],
            "tool_events": [],
            "response": "",
        },
        ExplodingModel(),
    )

    assert "证据不足" in result["response"]
    assert result["evidence_package"]["actionable_evidence_count"] == 0
    assert result["evidence_package"]["confidence"] == "low"


@pytest.mark.asyncio
async def test_replanner_appends_evidence_index_when_model_omits_ids(monkeypatch):
    class DummyPrompt:
        def __or__(self, other):
            return other

    class Chain:
        def __init__(self, schema):
            self.schema = schema
            self.payload = None

        async def ainvoke(self, payload):
            self.payload = payload
            return self.schema(response="# 报告\n\nCPU 当前正常。")

    class Model:
        def __init__(self):
            self.chain = None

        def with_structured_output(self, schema, method):
            self.chain = Chain(schema)
            return self.chain

    model = Model()
    payload = _metric_payload()

    monkeypatch.setattr(replanner_module, "response_prompt", DummyPrompt())
    monkeypatch.setattr(replanner_module.config, "aiops_structured_output_method", "function_calling")
    monkeypatch.setattr(replanner_module.config, "llm_timeout_seconds", 1)

    result = await replanner_module._generate_response(
        {
            "input": "诊断告警",
            "plan": [],
            "past_steps": [("查询 CPU", json.dumps(payload, ensure_ascii=False))],
            "tool_events": [],
            "response": "",
        },
        model,
    )

    assert "## 证据索引" in result["response"]
    assert "E001-metric" in result["response"]
    assert result["evidence_package"]["actionable_evidence_count"] == 1
    assert "Evidence Package" in model.chain.payload["messages"][2][1]


def test_aiops_public_exports_still_include_replanner():
    assert "replanner" in aiops_exports
    assert exported_replanner is not None
