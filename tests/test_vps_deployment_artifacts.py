from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_production_env_example_uses_placeholders_and_real_aiops_sources() -> None:
    content = read(".env.production.example")

    assert "LLM_API_KEY=replace-me" in content
    assert "AIOPS_ALLOW_MOCK=false" in content
    assert "AIOPS_LOG_PROVIDER=local_vps" in content
    assert "AIOPS_MONITOR_PROVIDER=prometheus" in content
    assert "AIOPS_PROMETHEUS_URL=http://prometheus:9090" in content
    assert "AIOPS_SERVICE_LOG_MAP=" in content
    assert "/var/log/oncall-agent/app.log" in content
    assert "/var/log/nginx/error.log" in content
    assert not re.search(r"sk-[A-Za-z0-9_-]{8,}", content)


def test_production_compose_contains_required_services_and_safe_bindings() -> None:
    content = read("docker-compose.prod.yml")

    for service in (
        "api:",
        "mcp-cls:",
        "mcp-monitor:",
        "prometheus:",
        "alertmanager:",
        "node-exporter:",
        "nginx:",
    ):
        assert service in content

    assert "127.0.0.1:${HOST_API_PORT:-9900}:9900" in content
    assert "127.0.0.1:${HOST_PROMETHEUS_PORT:-9090}:9090" in content
    assert "AIOPS_ALLOW_MOCK: \"false\"" in content
    assert "nginx-logs:/var/log/nginx:ro" in content
    assert "curl" in content, "compose healthchecks should use real HTTP probes"


def test_nginx_reverse_proxy_keeps_aiops_streaming_unbuffered() -> None:
    content = read("deploy/nginx/oncall-agent.conf")

    assert "proxy_pass http://api:9900" in content
    assert "location /api/aiops" in content
    assert "proxy_buffering off" in content
    assert "access_log /var/log/nginx/access.log" in content
    assert "error_log /var/log/nginx/error.log" in content


def test_vps_scripts_are_strict_and_check_real_endpoints() -> None:
    deploy = read("scripts/deploy_vps.sh")
    health = read("scripts/healthcheck.sh")

    assert "set -Eeuo pipefail" in deploy
    assert "set -Eeuo pipefail" in health
    assert "docker compose -f" in deploy
    assert "bash scripts/healthcheck.sh" in deploy
    assert "replace-me" in deploy
    assert "/health" in health
    assert "/api/aiops/self-check" in health
    assert "http://127.0.0.1:${HOST_MCP_CLS_PORT:-8003}/mcp" in health
    assert "Prometheus" in health
    assert "Alertmanager" in health


def test_vps_documentation_and_readme_cover_operational_flow() -> None:
    doc = read("docs/vps_deployment.md")
    readme = read("README.md")

    for keyword in ("首次部署", "健康检查", "日志接入", "指标", "nginx", "自启动", "回滚", "本轮验证边界"):
        assert keyword in doc

    assert "bash scripts/deploy_vps.sh" in readme
    assert "bash scripts/healthcheck.sh" in readme
    assert "docs/vps_deployment.md" in readme
