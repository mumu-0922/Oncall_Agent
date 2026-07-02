#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.production"
ENV_EXAMPLE=".env.production.example"

log() { printf '▶ %s\n' "$*"; }
err() { printf '❌ %s\n' "$*" >&2; }

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "缺少命令: $cmd"
    exit 127
  fi
}

require_cmd docker
require_cmd curl

if ! docker compose version >/dev/null 2>&1; then
  err "Docker Compose v2 不可用；请安装 docker compose 插件"
  exit 127
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  err "已生成 $ENV_FILE，请先填入真实 LLM_API_KEY / LLM_API_BASE 等配置后重新执行。"
  exit 2
fi

env_value() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  printf '%s' "${line#*=}"
}

is_placeholder() {
  local value="$1"
  [[ -z "$value" || "$value" == "replace-me" || "$value" == your-* || "$value" == *"your-gateway.example"* ]]
}

llm_provider="$(env_value LLM_PROVIDER)"
llm_api_key="$(env_value LLM_API_KEY)"
llm_api_base="$(env_value LLM_API_BASE)"
llm_model="$(env_value LLM_MODEL)"

if is_placeholder "$llm_api_key"; then
  err "$ENV_FILE 的 LLM_API_KEY 仍是占位符；请填入真实 LLM Key。"
  exit 2
fi

if [[ "$llm_provider" == "openai_compatible" ]] && is_placeholder "$llm_api_base"; then
  err "$ENV_FILE 的 LLM_API_BASE 仍是占位符；请填入真实 OpenAI-compatible base_url。"
  exit 2
fi

if is_placeholder "$llm_model"; then
  err "$ENV_FILE 的 LLM_MODEL 为空或占位；请填入真实模型名。"
  exit 2
fi

log "构建生产镜像"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build

log "启动生产服务"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --remove-orphans

log "当前容器状态"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps

log "运行健康检查"
bash scripts/healthcheck.sh

log "部署完成。Web 入口: http://<VPS_IP>:${NGINX_HTTP_PORT:-80}"
