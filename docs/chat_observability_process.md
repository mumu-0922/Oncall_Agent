# 聊天执行轨迹可视化改造过程

## 2026-06-23

- [x] 1. 完成参考项目检查。
  - 读取 `/home/mumu/projects/GenericAgent/agent_loop.py`。
  - 关键模式：每轮显示 `LLM Running`，工具调用前显示 `Tool + args`，工具输出做压缩展示。
  - 结论：本项目不应暴露原始 CoT，应展示可审计执行轨迹。

- [x] 2. 完成当前链路定位。
  - 后端入口：`app/api/chat.py`。
  - Agent 服务：`app/services/rag_agent_service.py`。
  - 前端聊天：`frontend/src/main.js`、`frontend/src/styles.css`。
  - 当前问题：`query_stream()` 只消费 `stream_mode="messages"` 的文本 token，没有消费 LangGraph `updates` 中的 tool calls / ToolMessage。

- [x] 3. 完成后端 trace schema 与脱敏工具。
  - 新增 `trace` event：`stage` / `tool_call` / `tool_result`。
  - 新增递归脱敏：敏感 key、token/key 值、长文本截断。
  - 非流式接口返回 `data.trace`，流式接口通过 SSE 推送 `type=trace`。
- [x] 4. 完成后端 SSE trace 输出。
  - `query_stream()` 切到 `stream_mode=["messages", "updates"]`。
  - `updates` 提取 tool_call / tool_result；`messages` 同时保留 token 流式输出。
  - 新增 `app/services/chat_trace_service.py`，把脱敏、工具事件解析、结果摘要从 Agent 编排中拆出。
- [x] 5. 完成前端执行轨迹 UI。
  - `frontend/src/main.js` 新增 `agent-trace` 折叠面板。
  - 快速模式展示 `data.trace`；流式模式实时追加 `type=trace` 事件。
  - `frontend/src/styles.css` 增加阶段、工具调用、工具结果、错误态样式。
- [x] 6. 完成测试与构建验证。
  - 新增 `tests/test_rag_agent_service.py` 用例：非流式 trace 脱敏、流式 trace/content 输出。
  - `ruff check` 通过。
  - `pytest tests/ -q`：31 passed。
  - `npm --prefix frontend run build`：通过并生成 `static/` 产物。
  - 质量复核：仓库无 `scripts/change_analyzer.js` / `scripts/quality_checker.js`，已人工检查 diff；可观测逻辑已拆到独立服务，避免污染 Agent 编排。
