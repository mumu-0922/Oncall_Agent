# VPS 部署闭环

> 目标：在一台 Linux VPS 上用 Docker Compose 启动 Oncall_Agent、CLS MCP、Monitor MCP、Prometheus、Alertmanager、node_exporter 与 nginx reverse proxy，并用健康检查脚本验证真实状态。
>
> 本文档不包含真实密钥。`.env.production.example` 只提供占位符，部署前必须复制为 `.env.production` 并填入真实配置。

## 1. 部署架构

```text
公网/浏览器
   │
   ▼
nginx :80
   │ proxy_pass
   ▼
FastAPI api :9900 ── MCP streamable-http ── CLS MCP :8003
       │                                  └─ 只读 AIOPS_SERVICE_LOG_MAP 白名单日志
       └── MCP streamable-http ─────────── Monitor MCP :8004
                                            ├─ Prometheus :9090
                                            └─ Alertmanager :9093

node_exporter :9100 ──被 Prometheus scrape，用于 CPU/内存/磁盘指标
```

默认端口策略：

- 对公网暴露：`NGINX_HTTP_PORT=80`
- 仅绑定 VPS 本机 `127.0.0.1`：API `9900`、MCP `8003/8004`、Prometheus `9090`、Alertmanager `9093`、node_exporter `9100`

## 2. 前置条件

VPS 需要：

- Linux x86_64/arm64
- Docker Engine + Docker Compose v2
- Git
- 可访问你的 LLM 网关或模型服务

快速检查：

```bash
docker --version
docker compose version
git --version
```

## 3. 首次部署

```bash
# 1. 拉代码
git clone <repository_url> Oncall_Agent
cd Oncall_Agent

# 2. 生成生产配置
cp .env.production.example .env.production
vim .env.production

# 3. 必填项：至少改掉这些占位符
# LLM_PROVIDER=openai_compatible
# LLM_API_BASE=https://your-gateway.example/v1
# LLM_API_KEY=replace-me
# LLM_MODEL=gpt-4o-mini

# 4. 一键构建 + 启动 + 健康检查
bash scripts/deploy_vps.sh
```

`deploy_vps.sh` 会拒绝仍含占位符的核心 LLM 配置，避免“看似部署成功、实际不能诊断”的假状态。

## 4. 手动部署命令

如果不使用脚本，可手动执行：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --remove-orphans
docker compose -f docker-compose.prod.yml --env-file .env.production ps
bash scripts/healthcheck.sh
```

## 5. 健康检查

```bash
bash scripts/healthcheck.sh
curl -sS http://127.0.0.1:9900/health
curl -sS http://127.0.0.1:9900/api/aiops/self-check
```

判定规则：

- `/health` 只证明 FastAPI 进程与基础依赖状态。
- `/api/aiops/self-check` 会做确定性探测：LLM 配置、MCP endpoint、工具加载、日志白名单、Prometheus/Alertmanager。
- self-check 返回 `degraded/unhealthy` 时，不要让 LLM 编报告，先看 `components` 里的真实错误原因。

## 6. 日志接入与权限

生产样例配置：

```env
AIOPS_LOG_PROVIDER=local_vps
AIOPS_ALLOW_MOCK=false
AIOPS_SERVICE_LOG_MAP={"oncall-agent":["/var/log/oncall-agent/app.log","/var/log/oncall-agent/mcp_cls.log","/var/log/oncall-agent/mcp_monitor.log"],"nginx":["/var/log/nginx/error.log","/var/log/nginx/access.log"]}
```

约束：

- `search_local_logs` 只读 `AIOPS_SERVICE_LOG_MAP` 白名单路径。
- 不扫描全盘，不读取 SSH key、云凭据、浏览器配置等无关路径。
- `AIOPS_ALLOW_MOCK=false` 是生产默认值；日志源缺失时返回真实错误，不返回假日志。

查看日志：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f --tail=120 api
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f --tail=120 mcp-cls
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f --tail=120 mcp-monitor
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f --tail=120 nginx
```

## 7. 指标与告警接入

生产样例配置：

```env
AIOPS_MONITOR_PROVIDER=prometheus
AIOPS_PROMETHEUS_URL=http://prometheus:9090
AIOPS_ALERTMANAGER_URL=http://alertmanager:9093
```

检查 Prometheus：

```bash
curl -sS http://127.0.0.1:9090/-/ready
curl -sS 'http://127.0.0.1:9090/api/v1/query?query=up'
curl -sS http://127.0.0.1:9093/api/v2/alerts
```

## 8. nginx reverse proxy

配置文件：`deploy/nginx/oncall-agent.conf`

关键点：

- `/health` 反代到 `api:9900/health`
- `/api/aiops` 关闭 `proxy_buffering`，避免 SSE 流式诊断被 nginx 缓住
- access/error log 写入 nginx volume，并通过白名单挂给 CLS MCP 只读查询

## 9. 自启动

推荐使用 Docker Compose 自带 restart policy：`restart: unless-stopped`。

如需 systemd 管理 Compose，可新增 `/etc/systemd/system/oncall-agent.service`：

```ini
[Unit]
Description=Oncall Agent Docker Compose
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/Oncall_Agent
RemainAfterExit=yes
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml --env-file .env.production up -d
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml --env-file .env.production down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now oncall-agent.service
sudo systemctl status oncall-agent.service
```

## 10. 更新与回滚

更新：

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
bash scripts/healthcheck.sh
```

回滚到上一个 Git 版本：

```bash
git log --oneline -5
git checkout <last_good_commit>
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
bash scripts/healthcheck.sh
```

## 11. 故障定位

```bash
# 容器状态
docker compose -f docker-compose.prod.yml --env-file .env.production ps

# API 健康检查
curl -sS http://127.0.0.1:9900/health

# AIOps 真实自诊断
curl -sS http://127.0.0.1:9900/api/aiops/self-check

# MCP 可达性：FastMCP streamable-http GET /mcp 返回 406 也算可达
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8003/mcp
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8004/mcp

# Prometheus / Alertmanager
curl -sS http://127.0.0.1:9090/-/ready
curl -sS http://127.0.0.1:9093/api/v2/status
```

## 12. 本轮验证边界

本仓库内已提供部署模板、脚本和静态测试。当前开发环境是 Win + WSL，不等同于一台全新的公网 VPS；首次上 VPS 后仍需按本文执行 `bash scripts/deploy_vps.sh` 与 `bash scripts/healthcheck.sh`，以真实运行结果为准。
