from app.services.log_analysis_service import LogAnalysisService


def test_log_analysis_filters_mcp_tool_noise():
    service = LogAnalysisService()
    logs = [
        {"level": "INFO", "message": "调用方法: search_local_logs", "file": "mcp_cls.log"},
        {"level": "INFO", "message": '"query": "ERROR OR timeout"', "file": "mcp_cls.log"},
        {"level": "INFO", "message": "返回结果摘要: {\"total\": 9}", "file": "mcp_cls.log"},
    ]

    result = service.analyze(logs)

    assert result["raw_count"] == 3
    assert result["signal_count"] == 0
    assert result["noise_count"] == 3
    assert result["top_fingerprints"] == []
    assert "未发现明确异常证据" in result["summary"]


def test_log_analysis_classifies_timeout_traceback_and_http_5xx():
    service = LogAnalysisService()
    logs = [
        {
            "timestamp": "2026-06-24 15:00:00",
            "level": "ERROR",
            "message": "2026-06-24 15:00:00 ERROR TimeoutError request_id=abc123",
            "file": "server.log",
        },
        {
            "timestamp": "2026-06-24 15:01:00",
            "level": "ERROR",
            "message": "Traceback (most recent call last): ValueError boom",
            "file": "server.log",
        },
        {
            "timestamp": "2026-06-24 15:02:00",
            "level": "ERROR",
            "message": "upstream returned HTTP 502 for /api/aiops",
            "file": "nginx.log",
        },
    ]

    result = service.analyze(logs)

    assert result["signal_count"] == 3
    assert result["categories"]["timeout"] == 1
    assert result["categories"]["traceback"] == 1
    assert result["categories"]["http_5xx"] == 1
    assert result["by_file"] == {"server.log": 2, "nginx.log": 1}
    assert any("timeout" in action.lower() for action in result["recommended_next_actions"])


def test_log_analysis_groups_similar_errors_by_fingerprint():
    service = LogAnalysisService()
    logs = [
        {
            "level": "ERROR",
            "message": "2026-06-24 15:00:01 ERROR worker failed request_id=req-111 user=10001",
            "file": "server.log",
        },
        {
            "level": "ERROR",
            "message": "2026-06-24 15:00:02 ERROR worker failed request_id=req-222 user=10002",
            "file": "server.log",
        },
    ]

    result = service.analyze(logs)

    assert result["signal_count"] == 2
    assert result["top_fingerprints"][0]["count"] == 2
    assert "<ts>" in result["top_fingerprints"][0]["fingerprint"]
    assert "<num>" in result["top_fingerprints"][0]["fingerprint"]
    assert "req-111" not in result["top_fingerprints"][0]["fingerprint"]
    assert "req-222" not in result["top_fingerprints"][0]["fingerprint"]


def test_log_analysis_reports_no_clear_signal_for_info_logs():
    service = LogAnalysisService()
    logs = [
        {"level": "INFO", "message": "service started", "file": "server.log"},
        {"level": "INFO", "message": "health check ok", "file": "server.log"},
    ]

    result = service.analyze(logs)

    assert result["signal_count"] == 0
    assert result["noise_count"] == 2
    assert result["recommended_next_actions"] == [
        "未发现明确异常证据，建议继续观察 self-check 与健康检查结果。"
    ]


def test_log_analysis_strips_ansi_before_fingerprint_and_classification():
    service = LogAnalysisService()
    logs = [
        {
            "level": "WARNING",
            "message": "\x1b[32m2026-06-24 15:00:01\x1b[0m | \x1b[33mWARNING\x1b[0m | memory high request_id=req-1",
            "file": "server.log",
        },
        {
            "level": "WARNING",
            "message": "\x1b[32m2026-06-24 15:00:02\x1b[0m | \x1b[33mWARNING\x1b[0m | memory high request_id=req-2",
            "file": "server.log",
        },
    ]

    result = service.analyze(logs)

    assert result["signal_count"] == 2
    assert result["categories"]["warn"] == 2
    assert result["categories"]["oom_memory"] == 2
    fingerprint = result["top_fingerprints"][0]["fingerprint"]
    assert result["top_fingerprints"][0]["count"] == 2
    assert "\x1b" not in fingerprint
    assert "<ts>" in fingerprint
