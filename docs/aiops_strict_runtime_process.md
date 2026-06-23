# AIOps Strict Runtime 修复过程记录

## 2026-06-23 - Phase 1：MCP 服务与模型能力实测

- 状态：完成
- 动作：
  - 前台启动 `mcp_servers/cls_server.py` 与 `mcp_servers/monitor_server.py`。
  - 用 `get_mcp_client_with_retry(force_new=True).get_tools()` 验证 MCP 工具加载。
  - 用当前 GPT 中转验证 structured output 与 tool calling。
- 结果：
  - CLS MCP 监听 `127.0.0.1:8003/mcp`。
  - Monitor MCP 监听 `127.0.0.1:8004/mcp`。
  - MCP 工具数：7。
  - 当前模型 `gpt-5.5` 通过：`function_calling`、`json_mode`、`json_schema` structured output。
  - 当前模型通过 `bind_tools()`，能返回 `tool_calls`。
- 记录：
  ```text
  tool_count= 7
  tool_names= ['get_current_timestamp', 'get_region_code_by_name', 'get_topic_info_by_name',
               'search_topic_by_service_name', 'search_log', 'query_cpu_metrics', 'query_memory_metrics']
  structured function_calling OK
  structured json_mode OK
  structured json_schema OK
  tool_call OK [{'name': 'echo_tool', ...}]
  ```

## 2026-06-23 - Phase 2：Strict 工具加载

- 状态：完成
- 改动：
  - `app/agent/aiops/utils.py`
- 内容：
  - 新增 `AIOpsDependencyError`、`AIOpsCapabilityError`、`AIOpsExecutionError`。
  - 新增 `load_aiops_tools_strict()`。
  - MCP 连接失败或工具为空时直接抛错，不返回空 MCP 工具。
- 决策：
  - 普通聊天可以降级；AIOps 诊断不降级，因为它必须依赖日志/监控证据。

## 2026-06-23 - Phase 3：Planner structured output 修复

- 状态：完成
- 改动：
  - `app/agent/aiops/planner.py`
- 内容：
  - 移除默认三步计划兜底。
  - 恢复 `llm.with_structured_output(Plan, method=...)`。
  - 空计划直接抛错。
  - 保留知识库检索为非必需参考；MCP 仍是必需依赖。

## 2026-06-23 - Phase 4：Executor 真实工具调用链修复

- 状态：完成
- 改动：
  - `app/agent/aiops/executor.py`
- 内容：
  - 移除“bind_tools 失败退回普通 LLM”。
  - 移除 `ToolNode` 依赖，改为读取 `AIMessage.tool_calls` 后手工执行对应工具。
  - 工具返回写成 `ToolMessage` 回填给模型，再让模型总结本步骤证据。
  - 若第一轮没有任何 `tool_calls` 且 `AIOPS_REQUIRE_TOOL_CALL=true`，直接失败。
- 修复的实测错误：
  ```text
  ValueError: Missing required config key 'N/A' for 'tools'.
  ```
- 验证结果：
  ```text
  检测到 1 个工具调用
  MCP 工具 query_cpu_metrics 调用成功
  工具 query_cpu_metrics 调用完成，输出长度: 1289
  步骤执行完成，工具调用 1 次，结果长度: 332
  ```

## 2026-06-23 - Phase 5：Replanner 与最终报告 strict 化

- 状态：完成
- 改动：
  - `app/agent/aiops/replanner.py`
- 内容：
  - 恢复 `with_structured_output(Act, method=...)`。
  - 恢复 `with_structured_output(Response, method=...)`。
  - 移除 structured output 失败后继续执行剩余计划的行为。
  - 移除“由于系统异常，无法生成完整响应”的兜底报告。
  - 没有执行证据时拒绝生成最终诊断报告。

## 2026-06-23 - Phase 6：单测补充

- 状态：完成
- 改动：
  - `tests/test_aiops_strict_runtime.py`
- 覆盖：
  - MCP 工具为空会抛 `AIOpsDependencyError`。
  - strict 工具加载会返回本地工具 + MCP 工具。
  - Executor 在模型不产生 tool_calls 时抛 `AIOpsCapabilityError`。
  - Planner 使用 structured output 正常返回计划。
  - Planner structured output 失败会抛错，不生成默认计划。
- 验证：
  ```bash
  .venv/bin/python -m pytest tests/test_aiops_strict_runtime.py -q -s
  ```
  结果：
  ```text
  5 passed
  ```

## 2026-06-23 - Phase 7：全量回归与端到端验证

- 状态：完成
- 改动：
  - `Makefile`
  - `docs/aiops_strict_runtime_plan.md`
  - `docs/aiops_strict_runtime_process.md`
- 内容：
  - 修复 MCP 后台启动链：`nohup` 在当前环境下会随 shell 退出，改为 `setsid ... < /dev/null &`。
  - 修复 stale PID 误判：`start-cls/start-monitor/status-mcp` 以 pid 文件 + `ps -p` 校验真实服务进程。
  - `status-mcp` 对 streamable-http 的裸 `GET /mcp` 返回 HTTP 406 视为端口存活，因为该协议要求 `Accept: text/event-stream`。
- 验证命令：
  ```bash
  make start-cls
  make start-monitor
  make status-mcp
  .venv/bin/python -m pytest tests/ -q
  ```
- 关键结果：
  ```text
  CLS MCP: 运行中，HTTP 406（端口存活）
  Monitor MCP: 运行中，HTTP 406（端口存活）
  MCP tools: tool_count=7
  Pytest: 29 passed
  ```
- 端到端验证：
  - `AIOpsService.execute()` 成功跑通：Planner → Executor → Replanner → Report → Complete。
  - Executor 真实调用 `query_cpu_metrics` MCP 工具。
  - Replanner structured output 决策为 `respond`。
  - 最终报告基于 CPU 工具证据输出。
  ```text
  EVENT= plan plan_created
  EVENT= step_complete step_executed
  MCP 工具 query_cpu_metrics 调用成功
  EVENT= report final_report
  EVENT= complete complete
  ```

## 当前剩余风险

- 当前 mock 监控数据带随机波动，端到端诊断结论数值会随运行变化；若要做稳定评估，应把 mock incident 固化成可回放事故库。
- `function_calling` 已在当前中转验证通过；如果换中转/模型，需要重新跑 structured output 与 tool calling 探测。
- RAG dense 当前因 `EMBEDDING_PROVIDER=disabled` 仍是 BM25-only，这是 RAG 配置选择，不影响 AIOps strict MCP 工具链。
