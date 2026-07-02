#!/usr/bin/env bash
set -Eeuo pipefail

load_env_file_defaults() {
  local env_file="${AIOPS_ENV_FILE:-.env.production}"
  [[ -f "$env_file" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    case "$key" in
      HOST_API_PORT|NGINX_HTTP_PORT|HOST_MCP_CLS_PORT|HOST_MCP_MONITOR_PORT|HOST_PROMETHEUS_PORT|HOST_ALERTMANAGER_PORT|HOST_NODE_EXPORTER_PORT)
        if [[ -z "${!key:-}" ]]; then
          printf -v "$key" '%s' "$value"
        fi
        ;;
    esac
  done < "$env_file"
}

load_env_file_defaults

BASE_URL="${AIOPS_BASE_URL:-http://127.0.0.1:${HOST_API_PORT:-9900}}"
NGINX_URL="${AIOPS_NGINX_URL:-http://127.0.0.1:${NGINX_HTTP_PORT:-80}}"
MCP_CLS_CHECK_URL="${MCP_CLS_CHECK_URL:-http://127.0.0.1:${HOST_MCP_CLS_PORT:-8003}/mcp}"
MCP_MONITOR_CHECK_URL="${MCP_MONITOR_CHECK_URL:-http://127.0.0.1:${HOST_MCP_MONITOR_PORT:-8004}/mcp}"
PROMETHEUS_CHECK_URL="${PROMETHEUS_CHECK_URL:-http://127.0.0.1:${HOST_PROMETHEUS_PORT:-9090}}"
ALERTMANAGER_CHECK_URL="${ALERTMANAGER_CHECK_URL:-http://127.0.0.1:${HOST_ALERTMANAGER_PORT:-9093}}"
CHECK_NGINX="${CHECK_NGINX:-true}"
CHECK_OBSERVABILITY="${CHECK_OBSERVABILITY:-true}"
TIMEOUT_SECONDS="${HEALTHCHECK_TIMEOUT_SECONDS:-8}"

failures=0

ok() { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*"; failures=$((failures + 1)); }

curl_code() {
  local url="$1"
  curl -sS -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT_SECONDS" "$url" 2>/dev/null || true
}

check_http_200() {
  local name="$1"
  local url="$2"
  local code
  code="$(curl_code "$url")"
  if [[ "$code" == "200" ]]; then
    ok "$name $url HTTP 200"
  else
    fail "$name $url HTTP ${code:-000}"
  fi
}

check_mcp_endpoint() {
  local name="$1"
  local url="$2"
  local code
  code="$(curl_code "$url")"
  case "$code" in
    200|400|405|406) ok "$name $url reachable (HTTP $code)" ;;
    *) fail "$name $url unreachable (HTTP ${code:-000})" ;;
  esac
}

check_self_check() {
  local tmp
  tmp="$(mktemp)"
  local code
  code="$(curl -sS -o "$tmp" -w '%{http_code}' --max-time "$TIMEOUT_SECONDS" "$BASE_URL/api/aiops/self-check" 2>/dev/null || true)"
  if [[ "$code" != "200" ]]; then
    fail "AIOps self-check $BASE_URL/api/aiops/self-check HTTP ${code:-000}"
    rm -f "$tmp"
    return
  fi

  local status="unknown"
  if command -v python3 >/dev/null 2>&1; then
    status="$(python3 - "$tmp" <<'PY' 2>/dev/null || true
import json, sys
path = sys.argv[1]
with open(path, encoding='utf-8') as f:
    payload = json.load(f)
print(payload.get('data', {}).get('status', 'unknown'))
PY
)"
  fi

  case "$status" in
    healthy) ok "AIOps self-check healthy" ;;
    degraded) warn "AIOps self-check degraded；请查看接口返回的 components 明细" ;;
    unhealthy) fail "AIOps self-check unhealthy；请查看接口返回的 components 明细" ;;
    *) ok "AIOps self-check HTTP 200（未解析 status，可查看原始返回）" ;;
  esac
  rm -f "$tmp"
}

printf '🔎 Oncall_Agent healthcheck\n'
printf '   API: %s\n' "$BASE_URL"

check_http_200 "FastAPI" "$BASE_URL/health"
check_self_check
check_mcp_endpoint "CLS MCP" "$MCP_CLS_CHECK_URL"
check_mcp_endpoint "Monitor MCP" "$MCP_MONITOR_CHECK_URL"

if [[ "$CHECK_OBSERVABILITY" == "true" ]]; then
  check_http_200 "Prometheus" "$PROMETHEUS_CHECK_URL/-/ready"
  check_http_200 "Alertmanager" "$ALERTMANAGER_CHECK_URL/api/v2/status"
fi

if [[ "$CHECK_NGINX" == "true" ]]; then
  check_http_200 "nginx reverse proxy" "$NGINX_URL/health"
fi

if [[ "$failures" -gt 0 ]]; then
  printf '\n❌ healthcheck failed: %s failure(s)\n' "$failures"
  exit 1
fi

printf '\n✅ healthcheck passed\n'
