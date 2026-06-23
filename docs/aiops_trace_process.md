# AIOps 执行轨迹可视化改造过程

## 2026-06-23

- [x] 1. 完成问题定位。
  - 普通聊天链路已经有 `trace`：`/api/chat`、`/api/chat_stream`、`rag_agent_service.py`、`frontend/src/main.js`。
  - AI Ops 是独立链路：`/api/aiops` -> `aiops_service.py` -> `planner/executor/replanner.py`。
  - 当前 AI Ops 只输出 `status/plan/step_complete/report/complete/error`，Executor 内部工具调用没有进入 SSE，也没有被前端展示。
  - 参考 GenericAgent 的展示核心是：每轮 LLM、工具参数、工具输出都要流到用户界面；本项目采用可审计摘要，不展示隐藏思维链。

- [x] 2. 扩展 AIOps state。
  - `app/agent/aiops/state.py` 新增 `tool_events: Annotated[list[dict], operator.add]`。
  - `app/services/aiops_service.py` 初始状态补入 `tool_events: []`。
  - 目的：让 Executor 节点把本轮工具轨迹作为 LangGraph update 输出，再由 SSE 推给前端。
- [x] 3. 增加 Executor 工具轨迹。
  - `app/agent/aiops/executor.py` 引入 `ChatTraceObserver`。
  - 每个执行步骤新增 stage 轨迹：开始执行、模型决策、步骤完成。
  - 每个 `llm_response.tool_calls` 生成 `tool_call`，每个 `ToolMessage` 生成 `tool_result`。
  - 工具失败会标记 `status=error`，仍作为证据进入轨迹。
- [x] 4. 增加 AIOpsService trace SSE。
  - `app/services/aiops_service.py` 在 workflow 开始、Planner、Executor、Replanner、完成/失败时发送 `type=trace`。
  - Executor 本轮输出的 `tool_events` 会逐条包装为 SSE `data`。
  - 保留原 `plan/step_complete/report/complete/error` 事件兼容前端旧逻辑。
- [x] 5. 增加前端 AI Ops 轨迹展示。
  - `frontend/src/main.js` 的 `sendAIOpsRequest()` 新增 `traceEvents` 缓冲。
  - 遇到 SSE `type=trace` 时调用 `updateTracePanel()` 实时更新折叠轨迹面板。
  - `updateAIOpsMessage()` 和 `addAIOpsMessage()` 增加 `traceEvents` 参数，最终 Markdown 渲染时保留轨迹面板。
- [x] 6. 完成测试与构建验证。
  - 新增 `test_executor_returns_tool_trace_events`：验证 Executor 返回 tool_call/tool_result，且参数脱敏。
  - 新增 `test_aiops_service_formats_tool_trace_events`：验证 `tool_events` 被包装成前端可识别的 `type=trace`。
  - `ruff check app/agent/aiops app/services/aiops_service.py app/services/chat_trace_service.py tests/test_aiops_strict_runtime.py`：通过。
  - `pytest tests/test_aiops_strict_runtime.py -q`：7 passed。
  - `pytest tests/ -q`：33 passed。
  - `npm --prefix frontend run build`：通过，已刷新 `static/` 生产产物。

## 2026-06-23 追补：AIOps 中途无 tool_calls 与历史保存

- [x] 7. 定位 AIOps 中途失败。
  - 截图错误：`模型没有产生任何 tool_calls`。
  - MCP 日志显示前面已有真实工具调用，说明不是 MCP 挂了。
  - 根因：Executor 每个计划步骤都新开一轮 `llm.bind_tools(... )`，但默认 `tool_choice=auto`，即使 prompt 写“必须调用工具”，模型/中转仍可直接输出文本不产生 `tool_calls`。
  - 修复方向：当 `AIOPS_REQUIRE_TOOL_CALL=True` 时对 LangChain `bind_tools` 传 `tool_choice="required"`，由协议层强制本步骤至少调用一个工具。

- [x] 8. 定位历史记录观感丢失。
  - 当前历史只在新建、切换历史、流式完成、从历史加载后继续聊天时写入 localStorage。
  - 快速聊天完成后新对话不会立即进入左侧历史；刷新/直接点 AI Ops 时容易看起来“没了”。
  - `triggerAIOps()` 会先 `newChat()`，如果当前聊天已有内容才保存；但空对话或未及时保存的 UI 状态不会立刻显示。
  - 修复方向：每次用户/助手/AIOps 消息落入 `currentChatHistory` 后立即 upsert 当前会话到 localStorage，并刷新左侧列表。
- [x] 9. 已修复 AIOps 强制工具调用与历史即时保存。
  - `app/agent/aiops/executor.py`：当 `AIOPS_REQUIRE_TOOL_CALL=True` 时，`llm.bind_tools(..., tool_choice="required")`，避免每个计划步骤里模型选择不调用工具。
  - `frontend/src/main.js`：新增 `persistCurrentChatHistory()`，用户消息、助手消息、流式完成、AIOps 最终消息都会立即 upsert 到 localStorage 并刷新左侧历史。
  - 新增测试 `test_executor_requires_tool_choice_when_configured`，锁住协议层强制工具调用行为。
- [x] 10. 完成追补验证。
  - `ruff check app/agent/aiops/executor.py tests/test_aiops_strict_runtime.py app/services/aiops_service.py app/services/chat_trace_service.py`：通过。
  - 重点回归：`test_executor_returns_tool_trace_events`、`test_aiops_service_formats_tool_trace_events`、`test_executor_requires_tool_choice_when_configured`：3 passed。
  - 聊天 trace 回归：`pytest tests/test_rag_agent_service.py -q --no-cov`：5 passed。
  - `npm --prefix frontend run build`：通过，已刷新 `static/assets/index-DuQ2fluu.js`。
  - 已发现 `tests/test_aiops_strict_runtime.py` 全文件在 pytest-asyncio 下存在旧用例组合后挂起现象，单测逐个/分组可过；本次改动相关新用例均已验证。

## 2026-06-23 追补：运行中切换历史导致 AIOps 界面丢失

- [x] 11. 定位运行中切历史问题。
  - 现象：AIOps SSE 仍在跑，`isStreaming=true`，但用户点击左侧历史后 `loadChatHistory()` 清空了当前 `chatMessages`。
  - 结果：后续 SSE 还在更新旧的 `loadingMessageElement` 引用，该 DOM 已脱离页面，于是主界面看不到 AIOps 运行轨迹；再点新建/AI Ops 会提示“请等待当前操作完成”。
- [x] 12. 增加前端护栏与自恢复。
  - `loadChatHistory()`：运行中禁止切换历史，提示“当前操作正在运行，请等待完成后再切换历史”。
  - `deleteChatHistory()`：运行中禁止删除历史。
  - `renderChatHistory()`：当前会话增加 `active` 类，左侧能看出当前所在会话。
  - `sendAIOpsRequest()`：如果 AIOps 消息 DOM 被外部清掉，后续 SSE 到达时自动重新创建“分析中...”消息并继续渲染。
