from __future__ import annotations

import importlib
from typing import Any


def _monitor():
    return importlib.import_module("mcp_servers.monitor_server")


def test_query_metric_instant_requires_prometheus_url(monkeypatch):
    monitor = _monitor()
    monkeypatch.setenv("AIOPS_MONITOR_PROVIDER", "prometheus")
    monkeypatch.delenv("AIOPS_PROMETHEUS_URL", raising=False)

    result = monitor.query_metric_instant("up")

    assert result["results"] == []
    assert result["history_available"] is False
    assert "Prometheus 未配置" in result["error"]
    assert "AIOPS_PROMETHEUS_URL" in result["error"]


def test_query_metric_instant_parses_vector(monkeypatch):
    monitor = _monitor()
    monkeypatch.setenv("AIOPS_PROMETHEUS_URL", "http://prom.example:9090")

    captured: dict[str, Any] = {}

    def fake_get(url, params=None, headers=None, timeout=20.0):
        captured.update({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"instance": "node-a"}, "value": [1780000000.0, "1"]},
                    {"metric": {"instance": "node-b"}, "value": [1780000000.0, "0"]},
                ],
            },
        }

    monkeypatch.setattr(monitor, "_http_get_json", fake_get)

    result = monitor.query_metric_instant("up", time="1780000000")

    assert captured["url"] == "http://prom.example:9090/api/v1/query"
    assert captured["params"] == {"query": "up", "time": "1780000000"}
    assert result["source"] == "prometheus:http://prom.example:9090"
    assert result["result_type"] == "vector"
    assert result["result_count"] == 2
    assert result["statistics"] == {"min": 0.0, "max": 1.0, "avg": 0.5, "p95": 1.0, "sample_count": 2}


def test_query_metric_range_parses_matrix_and_statistics(monkeypatch):
    monitor = _monitor()
    monkeypatch.setenv("AIOPS_PROMETHEUS_URL", "http://127.0.0.1:9090")

    def fake_get(url, params=None, headers=None, timeout=20.0):
        assert url == "http://127.0.0.1:9090/api/v1/query_range"
        assert params["query"] == "cpu_query"
        assert params["step"] == "60s"
        return {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"instance": "node-a"},
                        "values": [[1780000000.0, "10"], [1780000060.0, "20"], [1780000120.0, "30"]],
                    }
                ],
            },
        }

    monkeypatch.setattr(monitor, "_http_get_json", fake_get)

    result = monitor.query_metric_range(
        "cpu_query",
        start_time="1780000000",
        end_time="1780000120",
        step="60s",
    )

    assert result["history_available"] is True
    assert result["result_type"] == "matrix"
    assert result["series_count"] == 1
    assert result["point_count"] == 3
    assert result["statistics"]["max"] == 30.0
    assert result["statistics"]["avg"] == 20.0
    assert result["limited"] is False


def test_query_metric_range_adjusts_step_when_points_exceed_limit(monkeypatch):
    monitor = _monitor()
    monkeypatch.setenv("AIOPS_PROMETHEUS_URL", "http://127.0.0.1:9090")

    captured: dict[str, Any] = {}

    def fake_get(url, params=None, headers=None, timeout=20.0):
        captured.update(params or {})
        return {
            "status": "success",
            "data": {"resultType": "matrix", "result": [{"metric": {}, "values": [[0, "1"]]}]},
        }

    monkeypatch.setattr(monitor, "_http_get_json", fake_get)

    result = monitor.query_metric_range("q", start_time="0", end_time="600", step="10s", max_points=11)

    assert captured["step"] == "60s"
    assert result["limited"] is True
    assert "请求点数" in result["limit_reason"]
    assert result["requested_points_per_series"] == 61
    assert result["max_points"] == 11


def test_query_metric_range_returns_http_error_without_fake_data(monkeypatch):
    monitor = _monitor()
    monkeypatch.setenv("AIOPS_PROMETHEUS_URL", "http://127.0.0.1:9090")

    def fake_get(url, params=None, headers=None, timeout=20.0):
        return {"error": "HTTP 请求返回非 200 状态", "status_code": 500, "body": "boom"}

    monkeypatch.setattr(monitor, "_http_get_json", fake_get)

    result = monitor.query_metric_range("q", start_time="0", end_time="60", step="60s")

    assert result["results"] == []
    assert result["history_available"] is False
    assert result["status_code"] == 500
    assert result["body"] == "boom"
    assert "HTTP 请求返回非 200" in result["error"]


def test_query_cpu_metrics_uses_prometheus_template(monkeypatch):
    monitor = _monitor()
    monkeypatch.setenv("AIOPS_MONITOR_PROVIDER", "prometheus")
    monkeypatch.setenv("AIOPS_PROMETHEUS_URL", "http://127.0.0.1:9090")
    monkeypatch.setenv("AIOPS_PROMETHEUS_CPU_QUERY_TEMPLATE", "cpu_usage{service=\"{service_name}\"}")

    def fake_get(url, params=None, headers=None, timeout=20.0):
        assert params["query"] == 'cpu_usage{service="api"}'
        return {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {"metric": {"service": "api"}, "values": [[1780000000.0, "81"], [1780000060.0, "82"]]}
                ],
            },
        }

    monkeypatch.setattr(monitor, "_http_get_json", fake_get)

    result = monitor.query_cpu_metrics(
        "api",
        start_time="1780000000",
        end_time="1780000060",
        interval="60s",
    )

    assert result["metric_name"] == "cpu_usage_percent"
    assert result["history_available"] is True
    assert result["statistics"]["max"] == 82.0
    assert result["alert_info"]["triggered"] is True
    assert result["query"] == 'cpu_usage{service="api"}'


def test_list_active_alerts_requires_alertmanager_url(monkeypatch):
    monitor = _monitor()
    monkeypatch.delenv("AIOPS_ALERTMANAGER_URL", raising=False)

    result = monitor.list_active_alerts()

    assert result["alerts"] == []
    assert result["total"] == 0
    assert "Alertmanager 未配置" in result["error"]


def test_list_active_alerts_parses_alertmanager_v2(monkeypatch):
    monitor = _monitor()
    monkeypatch.setenv("AIOPS_ALERTMANAGER_URL", "http://alertmanager:9093")

    captured: dict[str, Any] = {}

    def fake_get(url, params=None, headers=None, timeout=20.0):
        captured.update({"url": url, "params": params})
        return [
            {
                "labels": {"alertname": "HighCPU", "severity": "critical", "instance": "node-a"},
                "annotations": {"summary": "cpu high"},
                "startsAt": "2026-06-23T11:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prom/graph?g0.expr=up",
                "fingerprint": "abc123",
                "status": {"state": "active", "silencedBy": [], "inhibitedBy": []},
                "receivers": [{"name": "default"}],
            }
        ]

    monkeypatch.setattr(monitor, "_http_get_json", fake_get)

    result = monitor.list_active_alerts(label_filter='severity="critical"')

    assert captured["url"] == "http://alertmanager:9093/api/v2/alerts"
    assert captured["params"] == {
        "active": "true",
        "silenced": "false",
        "inhibited": "false",
        "filter": 'severity="critical"',
    }
    assert result["source"] == "alertmanager:http://alertmanager:9093"
    assert result["total"] == 1
    assert result["alerts"][0]["alertname"] == "HighCPU"
    assert result["alerts"][0]["severity"] == "critical"
    assert result["alerts"][0]["status"] == "active"
    assert result["alerts"][0]["duration_seconds"] is not None


def test_query_alert_history_declares_history_unavailable(monkeypatch):
    monitor = _monitor()
    monkeypatch.setenv("AIOPS_ALERTMANAGER_URL", "http://alertmanager:9093")

    def fake_get(url, params=None, headers=None, timeout=20.0):
        return []

    monkeypatch.setattr(monitor, "_http_get_json", fake_get)

    result = monitor.query_alert_history(start_time="2026-06-23 10:00:00", end_time="2026-06-23 11:00:00")

    assert result["history_available"] is False
    assert result["alerts"] == []
    assert "不提供完整历史" in result["message"] or "只暴露当前" in result["message"]
