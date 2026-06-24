from __future__ import annotations

import importlib
import json
from datetime import datetime


def test_cls_local_vps_falls_back_to_default_service(monkeypatch):
    cls = importlib.import_module("mcp_servers.cls_server")
    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "local_vps")
    monkeypatch.setenv("AIOPS_DEFAULT_SERVICE", "real-app")
    monkeypatch.setenv(
        "AIOPS_SERVICE_LOG_MAP",
        '{"real-app":["/tmp/real-app.log"],"nginx":["/tmp/nginx.log"]}',
    )

    result = cls.search_topic_by_service_name("core-business-service")

    assert result["total"] == 1
    assert result["topics"][0]["service_name"] == "real-app"
    assert "fallback" in result["message"]


def test_cls_local_vps_reads_mapped_log_file(monkeypatch, tmp_path):
    cls = importlib.import_module("mcp_servers.cls_server")
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "2026-06-23 11:00:00 | INFO | boot ok\n"
        "2026-06-23 11:01:00 | ERROR | database timeout\n",
        encoding="utf-8",
    )
    start = int(datetime(2026, 6, 23, 11, 0, 0).timestamp() * 1000)
    end = int(datetime(2026, 6, 23, 11, 2, 0).timestamp() * 1000)

    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "local_vps")
    monkeypatch.setenv("AIOPS_SERVICE_LOG_MAP", f'{{"real-app":["{log_file}"]}}')

    result = cls.search_log("local:real-app", start, end, query="level:ERROR", limit=10)

    assert result["source"] == "local_vps:file"
    assert result["total"] == 1
    assert result["logs"][0]["level"] == "ERROR"
    assert "database timeout" in result["logs"][0]["message"]


def test_monitor_local_vps_uses_default_service_patterns(monkeypatch):
    monitor = importlib.import_module("mcp_servers.monitor_server")
    monkeypatch.setenv("AIOPS_DEFAULT_SERVICE", "real-app")
    monkeypatch.setenv(
        "AIOPS_SERVICE_PROCESS_MAP",
        '{"real-app":["uvicorn app.main:app"],"nginx":["nginx: worker"]}',
    )

    assert monitor._service_patterns("core-business-service") == (
        "real-app",
        ["uvicorn app.main:app"],
    )


def test_monitor_refuses_mock_without_explicit_allow(monkeypatch):
    monitor = importlib.import_module("mcp_servers.monitor_server")
    monkeypatch.setenv("AIOPS_MONITOR_PROVIDER", "mock")
    monkeypatch.setenv("AIOPS_ALLOW_MOCK", "false")

    result = monitor.query_cpu_metrics("demo-service")

    assert result["data_points"] == []
    assert result["source"] == "mock"
    assert "mock 监控数据已被禁用" in result["error"]


def test_monitor_allows_mock_only_when_explicit(monkeypatch):
    monitor = importlib.import_module("mcp_servers.monitor_server")
    monkeypatch.setenv("AIOPS_MONITOR_PROVIDER", "mock")
    monkeypatch.setenv("AIOPS_ALLOW_MOCK", "true")

    result = monitor.query_cpu_metrics("demo-service")

    assert result["data_points"]
    assert "error" not in result


def test_cls_refuses_mock_without_explicit_allow(monkeypatch):
    cls = importlib.import_module("mcp_servers.cls_server")
    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "mock")
    monkeypatch.setenv("AIOPS_ALLOW_MOCK", "false")

    result = cls.search_topic_by_service_name("demo-service")

    assert result["total"] == 0
    assert result["topics"] == []
    assert result["source"] == "mock"
    assert "mock 日志数据已被禁用" in result["error"]


def test_cls_allows_mock_only_when_explicit(monkeypatch):
    cls = importlib.import_module("mcp_servers.cls_server")
    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "mock")
    monkeypatch.setenv("AIOPS_ALLOW_MOCK", "true")

    result = cls.search_topic_by_service_name("data-sync-service")

    assert result["total"] >= 1
    assert "error" not in result


def test_search_local_logs_reads_service_log_without_topic(monkeypatch, tmp_path):
    cls = importlib.import_module("mcp_servers.cls_server")
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "2026-06-23 11:00:00 | INFO | boot ok\n"
        "2026-06-23 11:01:00 | WARN | slow request\n"
        "2026-06-23 11:02:00 | ERROR | llm timeout from gateway\n",
        encoding="utf-8",
    )
    start = int(datetime(2026, 6, 23, 11, 0, 0).timestamp() * 1000)
    end = int(datetime(2026, 6, 23, 11, 3, 0).timestamp() * 1000)

    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "local_wsl")
    monkeypatch.setenv("AIOPS_SERVICE_LOG_MAP", f'{{"super-biz-agent":["{log_file}"]}}')

    result = cls.search_local_logs(
        service_name="super-biz-agent",
        start_time=start,
        end_time=end,
        query="level:ERROR OR timeout",
        limit=10,
    )

    assert result["tool"] == "search_local_logs"
    assert result["source"] == "local_wsl:file"
    assert result["matched_service"] == "super-biz-agent"
    assert result["match_reason"] == "exact"
    assert result["history_available"] is True
    assert result["total"] == 1
    assert result["logs"][0]["level"] == "ERROR"
    assert "llm timeout" in result["logs"][0]["message"]
    assert result["scanned_files"] == [str(log_file)]


def test_search_local_logs_uses_default_service_when_service_unknown(monkeypatch, tmp_path):
    cls = importlib.import_module("mcp_servers.cls_server")
    log_file = tmp_path / "app.log"
    log_file.write_text("2026-06-23 11:00:00 | ERROR | default service boom\n", encoding="utf-8")
    start = int(datetime(2026, 6, 23, 10, 59, 0).timestamp() * 1000)
    end = int(datetime(2026, 6, 23, 11, 1, 0).timestamp() * 1000)

    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "local_vps")
    monkeypatch.setenv("AIOPS_DEFAULT_SERVICE", "real-app")
    monkeypatch.setenv(
        "AIOPS_SERVICE_LOG_MAP",
        json.dumps({"real-app": [str(log_file)], "nginx": [str(tmp_path / "nginx.log")]}),
    )

    result = cls.search_local_logs(
        service_name="core-business-service",
        start_time=start,
        end_time=end,
        query="ERROR",
    )

    assert result["matched_service"] == "real-app"
    assert result["match_reason"] == "default_service_fallback"
    assert result["total"] == 1
    assert "default service boom" in result["logs"][0]["message"]


def test_search_local_logs_refuses_unmapped_paths(monkeypatch):
    cls = importlib.import_module("mcp_servers.cls_server")
    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "local_wsl")
    monkeypatch.delenv("AIOPS_SERVICE_LOG_MAP", raising=False)

    result = cls.search_local_logs(service_name="../../.ssh", query="token")

    assert result["logs"] == []
    assert result["total"] == 0
    assert "不会自动扫描全盘" in result["error"]


def test_search_local_logs_marks_result_limit(monkeypatch, tmp_path):
    cls = importlib.import_module("mcp_servers.cls_server")
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "\n".join(
            f"2026-06-23 11:{minute:02d}:00 | ERROR | boom-{minute}"
            for minute in range(5)
        )
        + "\n",
        encoding="utf-8",
    )
    start = int(datetime(2026, 6, 23, 11, 0, 0).timestamp() * 1000)
    end = int(datetime(2026, 6, 23, 11, 5, 0).timestamp() * 1000)

    monkeypatch.setenv("AIOPS_LOG_PROVIDER", "local_wsl")
    monkeypatch.setenv("AIOPS_SERVICE_LOG_MAP", f'{{"app":["{log_file}"]}}')
    monkeypatch.setenv("AIOPS_LOCAL_LOG_MAX_RESULTS", "2")

    result = cls.search_local_logs("app", start, end, query="ERROR", limit=10)

    assert result["limit"] == 2
    assert result["limited"] is True
    assert result["total"] == 2
    assert "boom-4" in result["logs"][0]["message"]


def test_evidence_package_classifies_search_local_logs_as_log():
    service = importlib.import_module("app.services.evidence_package_service").EvidencePackageService()
    payload = {
        "tool": "search_local_logs",
        "service_name": "super-biz-agent",
        "matched_service": "super-biz-agent",
        "source": "local_wsl:file",
        "total": 1,
        "logs": [{"level": "ERROR", "message": "boom"}],
        "scanned_files": ["server.log"],
    }

    package = service.build_from_state(
        {
            "input": "查错误日志",
            "past_steps": [],
            "tool_events": [
                {
                    "kind": "tool_result",
                    "tool": "search_local_logs",
                    "status": "completed",
                    "summary": json.dumps(payload, ensure_ascii=False),
                }
            ],
        }
    )

    assert len(package.logs) == 1
    assert package.logs[0].tool_name == "search_local_logs"
    assert package.logs[0].metadata["matched_service"] == "super-biz-agent"
