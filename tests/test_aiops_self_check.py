from __future__ import annotations

import json

import pytest
from langchain_core.tools import tool

from app.api.aiops import self_check
from app.services.aiops_self_check_service import AIOpsSelfCheckService


@tool
async def search_local_logs(
    service_name: str | None = None,
    query: str | None = None,
    window_minutes: int = 60,
    limit: int = 10,
) -> str:
    """search local logs"""
    return json.dumps(
        {
            "tool": "search_local_logs",
            "source": "local_wsl:file",
            "matched_service": service_name,
            "match_reason": "exact",
            "query": query,
            "total": 1,
            "scanned_files": ["/tmp/app.log"],
            "logs": [
                {
                    "timestamp": "2026-06-24 14:00:00",
                    "level": "ERROR",
                    "file": "/tmp/app.log",
                    "message": "gateway timeout",
                }
            ],
            "limited": False,
            "took_ms": 2,
        },
        ensure_ascii=False,
    )


@tool
async def query_cpu_metrics(service_name: str = "app") -> str:
    """query cpu"""
    return "{}"


@tool
async def query_memory_metrics(service_name: str = "app") -> str:
    """query memory"""
    return "{}"


@tool
async def query_metric_instant(query: str = "up") -> str:
    """query instant"""
    return "{}"


@tool
async def list_active_alerts(label_filter: str | None = None) -> str:
    """list alerts"""
    return "{}"


async def _fake_load_tools(*args, **kwargs):
    return [], [
        search_local_logs,
        query_cpu_metrics,
        query_memory_metrics,
        query_metric_instant,
        list_active_alerts,
    ]


@pytest.fixture(autouse=True)
def isolate_external_probes(monkeypatch):
    async def fake_mcp_endpoint_component(self):
        return self._component("mcp_endpoints", "ok", "MCP endpoint 测试 stub", {})

    async def fake_prometheus_component(self):
        return self._component("prometheus", "skipped", "Prometheus 测试 stub", {})

    monkeypatch.setattr(
        AIOpsSelfCheckService,
        "_mcp_endpoint_component",
        fake_mcp_endpoint_component,
    )
    monkeypatch.setattr(
        AIOpsSelfCheckService,
        "_prometheus_component",
        fake_prometheus_component,
    )


@pytest.mark.asyncio
async def test_self_check_returns_real_log_evidence(monkeypatch, tmp_path):
    log_file = tmp_path / "app.log"
    log_file.write_text("2026-06-24 14:00:00 | ERROR | gateway timeout\n", encoding="utf-8")
    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "local_wsl")
    monkeypatch.setenv("AIOPS_DEFAULT_SERVICE", "super-biz-agent")
    monkeypatch.setenv(
        "AIOPS_SERVICE_LOG_MAP",
        json.dumps({"super-biz-agent": [str(log_file)]}),
    )
    monkeypatch.setenv("AIOPS_MONITOR_PROVIDER", "local_wsl")
    monkeypatch.setattr(
        "app.services.aiops_self_check_service.load_aiops_tools_strict",
        _fake_load_tools,
    )

    service = AIOpsSelfCheckService()
    result = await service.run()

    components = {component["name"]: component for component in result["components"]}
    assert components["local_log_config"]["status"] == "ok"
    assert components["search_local_logs"]["status"] == "ok"
    assert components["search_local_logs"]["details"]["source"] == "local_wsl:file"
    assert components["search_local_logs"]["details"]["total"] == 1
    assert "/tmp/app.log" in result["report"]
    assert "未调用 LLM" in result["report"]


@pytest.mark.asyncio
async def test_self_check_marks_missing_log_map_without_fake_data(monkeypatch):
    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "local_wsl")
    monkeypatch.delenv("AIOPS_SERVICE_LOG_MAP", raising=False)
    monkeypatch.setattr(
        "app.services.aiops_self_check_service.load_aiops_tools_strict",
        _fake_load_tools,
    )

    result = await AIOpsSelfCheckService().run()
    components = {component["name"]: component for component in result["components"]}

    assert result["status"] == "unhealthy"
    assert components["local_log_config"]["status"] == "error"
    assert "不会扫描全盘" in components["local_log_config"]["summary"]
    assert "不会补假数据" in result["report"]


@pytest.mark.asyncio
async def test_self_check_masks_secrets(monkeypatch, tmp_path):
    log_file = tmp_path / "app.log"
    log_file.write_text("2026-06-24 14:00:00 | ERROR | key sk-secret123456\n", encoding="utf-8")
    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "local_wsl")
    monkeypatch.setenv("AIOPS_DEFAULT_SERVICE", "super-biz-agent")
    monkeypatch.setenv("AIOPS_SERVICE_LOG_MAP", json.dumps({"super-biz-agent": [str(log_file)]}))

    @tool
    async def secret_log_tool(**kwargs) -> str:
        """secret log tool"""
        return json.dumps(
            {
                "tool": "search_local_logs",
                "source": "local_wsl:file",
                "matched_service": "super-biz-agent",
                "total": 1,
                "logs": [
                    {
                        "timestamp": "2026-06-24 14:00:00",
                        "level": "ERROR",
                        "file": str(log_file),
                        "message": "api_key=sk-secret123456 timeout",
                    }
                ],
                "scanned_files": [str(log_file)],
            }
        )

    async def fake_tools(*args, **kwargs):
        return [], [
            secret_log_tool,
            query_cpu_metrics,
            query_memory_metrics,
            query_metric_instant,
            list_active_alerts,
        ]

    secret_log_tool.name = "search_local_logs"
    monkeypatch.setattr("app.services.aiops_self_check_service.load_aiops_tools_strict", fake_tools)

    result = await AIOpsSelfCheckService().run()
    rendered = json.dumps(result, ensure_ascii=False)

    assert "sk-secret123456" not in rendered
    assert "***REDACTED***" in rendered


@pytest.mark.asyncio
async def test_aiops_self_check_api_uses_service(monkeypatch):
    async def fake_run():
        return {"status": "healthy", "components": [], "report": "# ok"}

    monkeypatch.setattr("app.api.aiops.aiops_self_check_service.run", fake_run)

    response = await self_check()

    assert response["code"] == 200
    assert response["message"] == "success"
    assert response["data"]["status"] == "healthy"
