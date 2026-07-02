# Interview Hardening Plan

> 目标：把 Oncall_Agent 从“可演示 AIOps/RAG Agent MVP”打磨成“面试可抗追问、能展示真实工程闭环、能量化验证”的项目。
> 规则：每完成一项，必须把对应 checklist 从 `[ ]` 改成 `[x]`，并在“过程记录”追加：改动、验证命令、结果、剩余风险。

## 0. 当前基线

已完成能力：

- [x] RAG `hybrid_parent`：parent-child chunk、BM25、dense 可选、score fusion/rerank、parent expansion。
- [x] RAG 离线评测：`hit_rate`、`recall@k`、`precision@k`、`MRR`、`latency_ms`。
- [x] AIOps strict runtime：MCP 不通、工具为空、模型不 tool_call 时显式失败，不生成假报告。
- [x] Evidence Package：工具证据归档，证据不足时返回证据不足。
- [x] AIOps Agent 离线评测：工具命中、证据覆盖、幻觉拦截、证据不足、timeout、耗时。
- [x] Prometheus / Alertmanager MCP 工具：instant/range/query alerts，失败返回真实原因。
- [x] `search_local_logs`：只读日志白名单，不扫描全盘，不读取无关敏感路径。
- [x] 日志摘要增强：确定性噪声过滤、错误分类、错误指纹聚合、最近问题摘要。
- [x] 前端执行轨迹：展示 tool_call / tool_result / evidence card。
- [x] 一键自诊断：`GET /api/aiops/self-check`，确定性探测，不调用 LLM 编结论。

当前限制：

- [ ] VPS 生产部署产物已补齐，但尚未在全新 VPS 执行从零部署验收。
- [ ] nginx / docker / systemd 日志接入未完成。
- [ ] Runbook 半自动自愈未完成。
- [ ] 公网部署鉴权、速率限制、安全加固未完成。

---

## P1. 日志摘要增强

### 面试目标

能讲清：不是把原始日志直接塞给 LLM，而是先做确定性噪声过滤、错误分类和指纹聚合，减少 token 消耗与幻觉风险。

### 交付物

- [x] 新增 `app/services/log_analysis_service.py`
- [x] 新增 `tests/test_log_analysis_service.py`
- [x] `AIOpsSelfCheckService` 集成日志分析结果
- [x] 自诊断报告展示“最近问题摘要”
- [x] README 或 docs 补充日志分析设计说明

### 功能 checklist

- [x] 过滤 MCP 工具调用噪声：
  - `调用方法`
  - `参数信息`
  - `返回结果摘要`
  - `"query": "ERROR OR timeout"`
  - `search_local_logs` 参数回显
- [x] 日志分类：
  - `ERROR`
  - `WARN`
  - `timeout`
  - `Traceback`
  - `HTTP 5xx`
  - `connection refused / ConnectTimeout`
  - `OOM / memory`
- [x] 错误指纹：
  - 去时间戳
  - 去 UUID / request id
  - 去长数字
  - 聚合同类错误
- [x] 输出结构：
  - `raw_count`
  - `signal_count`
  - `noise_count`
  - `categories`
  - `top_fingerprints`
  - `sampled_evidence`
  - `recommended_next_actions`
- [x] 无有效异常时明确输出：`未发现明确异常证据`

### 设计说明

- `LogAnalysisService` 是确定性规则模块，不调用 LLM，不编造根因。
- 输入只来自 `search_local_logs` 返回的白名单日志结果；不主动扫描系统目录。
- 先过滤 MCP 工具调用、query 回显、成功摘要等低信号日志，再对 ERROR/WARN/timeout/Traceback/HTTP 5xx/connection/OOM 等信号分类。
- 指纹聚合会去掉时间戳、UUID、request/trace/span/session id、长数字和超长值，用于把同类错误合并成 `top_fingerprints`。
- 报告只输出“发现了什么证据”和“建议下一步查什么”，不把规则命中直接包装成确定根因。

### 验收命令

```bash
.venv/bin/python -m ruff check app/services/log_analysis_service.py app/services/aiops_self_check_service.py tests/test_log_analysis_service.py tests/test_aiops_self_check.py
.venv/bin/python -m pytest tests/test_log_analysis_service.py tests/test_aiops_self_check.py -q
curl -sS http://127.0.0.1:9900/api/aiops/self-check
```

### 完成判定

- [x] 自诊断报告顶部出现“最近问题摘要”
- [x] 噪声日志不会进入 top_fingerprints
- [x] 测试覆盖噪声过滤、timeout 识别、Traceback 识别、同类聚合、无异常分支

---

## P2. AIOps Agent 量化评测

### 面试目标

能讲清：不仅做了 Agent，还做了离线评测集，能量化工具调用、证据覆盖、幻觉拦截和耗时。

### 交付物

- [x] 新增 `evals/aiops_cases.json`
- [x] 新增 `scripts/eval_aiops_agent.py`
- [x] 新增 `evals/reports/aiops_agent_eval.json`
- [x] 新增 `tests/test_aiops_eval_metrics.py`
- [x] Makefile 新增 `make eval-aiops`

### 指标 checklist

- [x] `tool_call_success_rate`
- [x] `expected_tool_hit_rate`
- [x] `evidence_coverage`
- [x] `hallucination_block_rate`
- [x] `insufficient_evidence_rate`
- [x] `avg_latency_ms`
- [x] `timeout_rate`

### Case checklist

- [x] 正常日志查询：必须调用 `search_local_logs`
- [x] Prometheus 指标查询：必须调用 `query_cpu_metrics` / `query_memory_metrics`
- [x] MCP 不可用：必须返回真实错误
- [x] 日志源未配置：必须返回证据不足
- [x] 禁止无证据输出“内存泄漏/CPU 死循环”等根因

### 设计说明

- 默认评测模式为 `offline_trace`，不调用 LLM、不访问真实 VPS、Prometheus 或 MCP，避免把网络/中转波动混进评测指标。
- 评测输入是可回放的 AIOps trace case：`tool_call`、`tool_result`、`final_response`、期望工具、期望证据类型、禁止声明。
- 评测先通过 `EvidencePackageService` 把工具结果转成 Evidence Package，再量化工具命中、证据引用、证据不足拦截、timeout 与错误路径。
- 工具错误也算证据类型 `tool_error`，报告必须引用 `E001-tool_error` 这类证据 ID，不能只口头说失败。
- `tool_call_success_rate` 会把故意设计的失败路径计入分母；因此当前 0.5 是真实反映：6 个 case 中 3 个是 MCP 不可用、日志源缺失、Prometheus timeout。

### 当前离线评测结果

来源：`evals/reports/aiops_agent_eval.json`

```json
{
  "tool_call_success_rate": 0.5,
  "expected_tool_hit_rate": 1.0,
  "expected_evidence_kind_hit_rate": 1.0,
  "evidence_coverage": 1.0,
  "hallucination_block_rate": 1.0,
  "insufficient_evidence_rate": 0.3333,
  "timeout_rate": 0.1667,
  "outcome_match_rate": 1.0,
  "expected_error_message_rate": 1.0,
  "avg_latency_ms": 0.1717
}
```

### 验收命令

```bash
make eval-aiops
.venv/bin/python -m pytest tests/test_aiops_eval_metrics.py -q
```

### 完成判定

- [x] 生成稳定 JSON 报表
- [x] 报表包含所有指标
- [x] README 能引用一组真实指标

---

## P3. VPS 部署闭环

### 面试目标

能讲清：项目不是只在本地跑，具备 VPS 部署、健康检查、日志/指标接入和回滚说明。

### 交付物

- [x] 新增 `.env.production.example`
- [x] 新增 `docker-compose.prod.yml`
- [x] 新增 `scripts/deploy_vps.sh`
- [x] 新增 `scripts/healthcheck.sh`
- [x] 新增 `docs/vps_deployment.md`
- [x] README 增加 VPS 部署章节

### 部署 checklist

- [x] FastAPI 后端
- [x] CLS MCP
- [x] Monitor MCP
- [x] Prometheus
- [x] node_exporter
- [x] Alertmanager
- [x] nginx reverse proxy
- [x] 日志目录与权限说明
- [x] systemd 或 Docker Compose 自启动说明

### 验收命令

```bash
bash scripts/healthcheck.sh
curl -sS http://127.0.0.1:9900/health
curl -sS http://127.0.0.1:9900/api/aiops/self-check
```

### 完成判定

- [ ] 新 VPS 可按文档从零启动
- [ ] self-check 能显示真实 VPS 环境状态
- [x] 部署失败时有明确错误定位步骤

---

## P4. nginx / Docker / systemd 日志接入

### 面试目标

能讲清：日志不止项目自身，还能接真实服务日志，并且通过白名单与权限控制避免越界读取。

### 交付物

- [ ] `.env.production.example` 增加日志映射样例
- [ ] `docs/log_sources.md`
- [ ] 自诊断展示每个日志源可读性
- [ ] 测试覆盖不存在路径、权限不足、合法路径读取

### 日志源 checklist

- [ ] nginx access/error
- [ ] Docker `json-file` 容器日志
- [ ] systemd journal 导出或服务日志文件
- [ ] 应用自定义日志目录

### 完成判定

- [ ] 能用服务名查询 nginx 错误日志
- [ ] 能用服务名查询 Docker 容器日志
- [ ] 不允许未配置路径读取

---

## P5. Runbook 半自动自愈

### 面试目标

能讲清：诊断之后可生成动作建议，但写操作有风险分级、审批、dry-run 和执行后验证。

### 交付物

- [ ] 新增 `app/services/runbook_action_service.py`
- [ ] 新增 `app/api/runbook.py`
- [ ] 新增 `docs/runbook_actions.md`
- [ ] 前端展示“建议动作”
- [ ] 测试覆盖危险动作拦截

### 动作分级

- [ ] `readonly`：查看端口、进程、磁盘、日志
- [ ] `safe_restart`：重启 MCP、reload nginx
- [ ] `dangerous`：删除文件、kill 进程、docker prune

### 安全 checklist

- [ ] 默认只读
- [ ] 写操作必须显式确认
- [ ] 支持 dry-run
- [ ] 执行后自动跑 self-check
- [ ] 所有命令白名单化

---

## P6. 安全与权限

### 面试目标

能讲清：AIOps Agent 能读日志/查指标/执行动作，因此必须做访问控制、路径白名单、脱敏和限流。

### 交付物

- [ ] API Key 鉴权
- [ ] CORS 白名单
- [ ] 日志路径白名单校验增强
- [ ] 敏感字段脱敏统一工具
- [ ] 请求速率限制
- [ ] 前端访问保护
- [ ] 安全测试

### 验收命令

```bash
.venv/bin/python -m pytest tests/test_security_*.py -q
```

---

## 过程记录

### 2026-06-24 - 建立面试增强计划

- 改动：新增 `docs/interview_hardening_plan.md`
- 当前状态：P0 基线已完成；P1~P6 待推进
- 下一步：开始 P1 日志摘要增强
- 验证：文档新增，无运行时代码变更

### 2026-06-24 - P1 日志摘要增强

- 改动：
  - 新增 `app/services/log_analysis_service.py`，支持噪声过滤、异常分类、错误指纹聚合、建议动作生成。
  - `AIOpsSelfCheckService` 集成日志分析结果，并在 Markdown 报告顶部展示“最近问题摘要”。
  - 报告样例日志只展示有效异常信号，过滤 query 回显等噪声；同时清理 ANSI 颜色控制符，避免影响指纹聚合。
  - 新增 `tests/test_log_analysis_service.py`，更新 `tests/test_aiops_self_check.py` 覆盖 self-check 报告输出。
- 验证：
  - `.venv/bin/python -m ruff check app/services/log_analysis_service.py app/services/aiops_self_check_service.py tests/test_log_analysis_service.py tests/test_aiops_self_check.py`
    - 结果：`All checks passed!`
  - `.venv/bin/python -m pytest tests/test_log_analysis_service.py tests/test_aiops_self_check.py -q`
    - 结果：`10 passed`
  - `.venv/bin/python -m pytest tests/test_log_analysis_service.py tests/test_aiops_self_check.py tests/test_local_vps_mcp.py tests/test_prometheus_mcp.py -q`
    - 结果：`31 passed`
- 剩余风险：
  - 当前为规则分析，不是 ML/模型分类；复杂业务错误需要继续扩展分类规则。
  - 当前只分析 `search_local_logs` 返回的日志样本，受 `limit` 与日志白名单配置影响。

### 2026-06-24 - P2 AIOps Agent 量化评测

- 改动：
  - 新增 `evals/aiops_cases.json`，覆盖日志查询、CPU/内存指标、MCP 不可用、日志源缺失、无证据禁幻觉、Prometheus timeout。
  - 新增 `scripts/eval_aiops_agent.py`，以离线 trace benchmark 评估工具命中、证据覆盖、幻觉拦截、证据不足、timeout 与耗时。
  - 新增 `tests/test_aiops_eval_metrics.py`，覆盖核心指标、失败路径、timeout 统计和 analyzer lazy import。
  - Makefile 新增 `make eval-aiops`，输出 `evals/reports/aiops_agent_eval.json`。
  - `app.agent.aiops` 改为 lazy public exports，避免离线评测导入 analyzer 时触发 Planner/Replanner/RAG 初始化副作用。
- 验证：
  - `make eval-aiops`
    - 结果：生成 `evals/reports/aiops_agent_eval.json`
  - `.venv/bin/python -m pytest tests/test_aiops_eval_metrics.py -q`
    - 结果：`7 passed`
  - `.venv/bin/python -m pytest tests/test_aiops_eval_metrics.py tests/test_aiops_evidence_package.py tests/test_aiops_analyzers.py tests/test_aiops_strict_runtime.py tests/test_aiops_self_check.py tests/test_log_analysis_service.py tests/test_local_vps_mcp.py tests/test_prometheus_mcp.py -q`
    - 结果：`64 passed`
  - `.venv/bin/python -m ruff check app/agent/aiops/__init__.py app/services/aiops_service.py scripts/eval_aiops_agent.py tests/test_aiops_eval_metrics.py`
    - 结果：`All checks passed!`
- 当前指标：
  - `tool_call_success_rate=0.5`
  - `expected_tool_hit_rate=1.0`
  - `evidence_coverage=1.0`
  - `hallucination_block_rate=1.0`
  - `insufficient_evidence_rate=0.3333`
  - `timeout_rate=0.1667`
  - `outcome_match_rate=1.0`
  - `avg_latency_ms≈0.17`
- 剩余风险：
  - 当前是离线 trace benchmark，不代表真实 LLM 每次都会选择同样工具；后续可扩展 live-agent eval。
  - 当前 case 数为 6，覆盖关键链路但还不算大规模评测集。
  - `tool_call_success_rate=0.5` 包含故意设计的错误/超时 case，不能解读为系统正常场景只有 50% 成功率。


### 2026-06-24 - P3 VPS 部署闭环

- 改动：
  - 新增 `.env.production.example`，提供生产占位配置，默认 `AIOPS_ALLOW_MOCK=false`，日志使用 `AIOPS_SERVICE_LOG_MAP` 白名单，指标使用 Prometheus/Alertmanager。
  - 新增 `Dockerfile` 与 `docker-compose.prod.yml`，编排 FastAPI、CLS MCP、Monitor MCP、Prometheus、Alertmanager、node_exporter、nginx。
  - 新增 `deploy/nginx/oncall-agent.conf`，反代 FastAPI，并对 `/api/aiops` 关闭 buffering 以支持 SSE。
  - 新增 `scripts/deploy_vps.sh`，部署前校验 `.env.production` 中核心 LLM 配置是否仍为占位符，拒绝假部署。
  - 新增 `scripts/healthcheck.sh`，检查 `/health`、`/api/aiops/self-check`、MCP endpoint、Prometheus、Alertmanager、nginx。
  - 新增 `docs/vps_deployment.md`，覆盖首次部署、健康检查、日志/指标接入、nginx、systemd 自启动、更新与回滚。
  - `app/utils/logger.py` 支持 `APP_LOG_FILE`，MCP 服务支持 `MCP_CLS_HOST/PORT/LOG_FILE` 与 `MCP_MONITOR_HOST/PORT/LOG_FILE`，适配容器生产运行。
  - 新增 `tests/test_vps_deployment_artifacts.py`，防止部署产物缺失、误写真实密钥、漏掉真实健康检查路径。
- 验证：
  - 待本轮运行：`ruff`、`pytest tests/test_vps_deployment_artifacts.py`、`docker compose config`、`scripts/healthcheck.sh`。
- 剩余风险：
  - 当前是在 Win + WSL 开发环境内准备部署闭环，不等同于新 VPS 已真实上线。
  - `新 VPS 可按文档从零启动` 与 `self-check 能显示真实 VPS 环境状态` 仍需在目标 VPS 上执行 `bash scripts/deploy_vps.sh && bash scripts/healthcheck.sh` 后再打勾。
  - 公网访问鉴权、HTTPS、CORS 白名单、限流仍属于 P6。
