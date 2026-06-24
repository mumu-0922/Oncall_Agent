# Open-source Reference & Magic-refactor Plan

> 目标：参考高星 AIOps / SRE Agent / RAG / LLMOps 项目，把 Oncall_Agent 从“会调用框架的 demo”推进成“真实证据驱动、可量化评估、可解释诊断”的工程项目。

## 1. 参考仓库隔离约束

- 所有外部仓库只放在 `reference-projects/`。
- `reference-projects/` 必须被 `.gitignore` 忽略，禁止提交第三方源码。
- 主工程不得从 `reference-projects/` import 任何代码。
- 不做大段复制；要先提炼接口/流程/数据结构，再用本项目代码风格重写。
- 若确需借鉴极小片段，必须在设计文档记录：repo、commit、license、原路径、改写路径、改写说明。
- 魔改落点只允许进入本项目模块：`app/`、`mcp_servers/`、`evals/`、`scripts/`、`frontend/`、`docs/`、`tests/`。

## 2. 项目取经清单

| 优先级 | 项目 | 取经点 | 落地到本项目 |
|---:|---|---|---|
| 1 | HolmesGPT | 真实观测数据 toolset、RCA、tool output budget、read-only guardrails | `mcp_servers/*`、`app/agent/aiops/*` |
| 2 | K8sGPT | analyzer-first，先确定性扫描再 AI 解释 | `app/agent/aiops/analyzers.py`、Evidence Package |
| 3 | Keep | 告警聚合、去重、关联、enrichment、workflow | `app/services/alert_*`、`/api/alerts`、前端告警页 |
| 4 | RAGFlow | 文档解析、chunk 可视化、citation、知识库 pipeline | `app/services/*retrieval*`、`/api/rag/*` |
| 5 | Langfuse | LLM trace、prompt version、dataset/eval、latency/cost | `chat_trace_service`、`llm_factory`、`evals` |
| 6 | Promptfoo | prompt/agent/RAG 回归评测、red-team gates | `scripts/eval_*`、CI gates |
| 7 | Ragas | faithfulness、context precision/recall | `evals/rag_quality_*` |

## 3. 魔改路线

### Phase 0：立约束与测试

- 建立 `reference-projects/manifest.json`。
- 增加 contract tests：
  - 参考目录必须 gitignored。
  - manifest 必须列出必学项目。
  - 主工程不得 import/reference `reference-projects`。
  - 计划文档必须声明“证据优先、禁止假数据、禁止 vendor copy”。

### Phase 1：Evidence Package

目标：报告只能从证据包生成，没证据就写“证据不足”。

拟新增：

```text
app/models/evidence.py
app/services/evidence_package_service.py
```

核心结构：

```text
EvidencePackage
  - incident_id
  - time_range
  - service
  - alerts[]
  - metrics[]
  - logs[]
  - runbooks[]
  - tool_errors[]
  - confidence
```

### Phase 2：Analyzer-first

目标：学习 K8sGPT，先由确定性 analyzer 输出 findings，再交给 LLM 写人话。

拟新增：

```text
app/agent/aiops/analyzers/
  cpu_high.py
  memory_high.py
  disk_high.py
  process_down.py
  log_error_spike.py
```

Analyzer 输出：

```text
Finding(status, severity, summary, evidence_refs, next_queries)
```

### Phase 3：真实观测数据源

目标：学习 HolmesGPT，把 WSL `/proc` demo 升级为 VPS 真实观测链。

拟新增 MCP 工具：

```text
list_active_alerts
query_metric_instant
query_metric_range
query_alert_history
search_service_logs
```

首选接入：

```text
Prometheus + node_exporter + Alertmanager
```

### Phase 4：告警中心与 workflow

目标：学习 Keep，实现可操作的告警页面和诊断入口。

拟新增：

```text
/api/alerts
/api/incidents
/api/reports
frontend alerts/incidents/reports views
```

### Phase 5：LLMOps trace & eval gates

目标：学习 Langfuse / Promptfoo / Ragas，把“做了有用吗”量化。

拟新增指标：

```text
retrieval_recall@5
context_precision
faithfulness
tool_call_success_rate
evidence_coverage
hallucination_block_rate
latency_p95
timeout_rate
```

## 4. 不做事项

- 不把 Keep / HolmesGPT / K8sGPT 变成本项目运行时依赖。
- 不直接嵌入第三方源码目录。
- 不先堆 UI；先补真实数据、证据包、评估。
- 不让 LLM 在工具失败时补故事。

## 5. 面试讲法

> 这个项目不是简单 LangChain demo。我参考 HolmesGPT 的真实观测数据接入、K8sGPT 的 analyzer-first、Keep 的告警关联、RAGFlow 的可解释检索和 Langfuse/Promptfoo/Ragas 的评估体系，重写成一个 evidence-grounded AIOps Agent。它的核心约束是：工具证据先行、报告可追溯、失败不伪造、效果可量化。
