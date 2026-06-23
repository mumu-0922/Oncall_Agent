# AIOps Strict Runtime 修复计划

> 目标：按“不要降级，先修复目前没实现的”要求，把 AIOps 从假继续/假报告改成真实 MCP + tool calling + structured output 链路。

## 问题判定

日志暴露的核心问题不是 RAG/BM25，而是 AIOps 执行链未闭环：

- MCP 服务未真实连通时，Planner/Executor/Replanner 继续降级，生成默认步骤。
- `with_structured_output()` 失败时被普通 JSON/text parser 替代，导致 `continue`、Markdown 报告被当 JSON 校验失败。
- Executor 的 tool calling 未被证明真实执行；原 `ToolNode` 在当前运行方式下出现 `Missing required config key 'N/A' for 'tools'`。
- 最终报告缺少“必须来自工具证据”的硬约束。

## 设计原则

- AIOps 不做 silent fallback：依赖不可用就显式失败。
- MCP 是 AIOps 必需依赖：CLS/Monitor 工具必须加载到。
- LLM 必须支持 structured output：Planner/Replanner/Response 都走 `with_structured_output()`。
- LLM 必须支持 tool calling：Executor 必须产生并执行 `tool_calls`。
- 任何空计划、空结果、空报告都中断，不生成伪结果。

## 执行清单

- [x] Phase 1：确认 MCP 服务真实可启动、工具可加载。
- [x] Phase 2：把 AIOps 工具加载改为 strict，不再返回空 MCP 工具继续跑。
- [x] Phase 3：把 Planner 恢复为 structured output，失败显式报错。
- [x] Phase 4：把 Executor 修成真实 tool calling 执行链。
- [x] Phase 5：把 Replanner/最终报告恢复为 structured output，去掉失败兜底报告。
- [x] Phase 6：增加单测覆盖 strict 行为。
- [x] Phase 7：全量回归与 API/SSE 链路验证。

## 关键配置

```env
AIOPS_STRUCTURED_OUTPUT_METHOD=function_calling
AIOPS_REQUIRE_TOOL_CALL=true
AIOPS_TOOL_CALL_MAX_ROUNDS=3
MCP_CLS_TRANSPORT=streamable-http
MCP_CLS_URL=http://localhost:8003/mcp
MCP_MONITOR_TRANSPORT=streamable-http
MCP_MONITOR_URL=http://localhost:8004/mcp
```

## 验收标准

- MCP 工具加载结果必须包含 CLS/Monitor 工具，当前期望 7 个 MCP 工具：
  - `get_current_timestamp`
  - `get_region_code_by_name`
  - `get_topic_info_by_name`
  - `search_topic_by_service_name`
  - `search_log`
  - `query_cpu_metrics`
  - `query_memory_metrics`
- 当前中转模型必须通过：
  - `with_structured_output(..., method=function_calling)`
  - `bind_tools([...])` 并返回 `tool_calls`
- Executor 至少执行一次 MCP 工具调用，否则 AIOps 失败。
- Planner 不再返回默认计划。
- Executor 不再把异常写成 `执行失败: ...` 继续后续流程。
- Replanner 不再把 structured output 错误吞掉后继续执行。
- 最终响应生成失败不再返回“由于系统异常”的伪报告。
