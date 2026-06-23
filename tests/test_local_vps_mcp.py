from __future__ import annotations

import importlib
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
