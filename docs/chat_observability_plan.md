# 聊天执行轨迹可视化改造计划

## 目标

让普通聊天与流式聊天能看到 Agent 的可审计执行轨迹：阶段状态、工具调用、工具参数摘要、工具结果摘要、检索结果摘要。借鉴 GenericAgent 的“LLM turn + Tool args + Tool output”展示方式，但不暴露模型隐藏思维链。

## 边界

- 展示“思考摘要 / 执行轨迹”，不展示原始 chain-of-thought。
- 工具参数与结果必须脱敏、截断，避免泄露 API Key、Token、Cookie、大段日志。
- 优先改 `frontend/src/*` 与 `app/services/rag_agent_service.py`，`static/` 由前端 build 生成。
- 保持现有 `/api/chat` 与 `/api/chat_stream` 响应兼容。

## 借鉴点

- GenericAgent 在 `agent_loop.py` 中每轮输出 `LLM Running (Turn n)`。
- 工具调用前输出 `🛠️ Tool: ... args`。
- 工具执行结果被包在代码块里，长内容会压缩展示。
- `plugins/hooks.py` / `langfuse_tracing.py` 使用 `tool_before/tool_after` 做可观测 span。

## 改造步骤

- [x] 1. 对照 GenericAgent 与当前聊天链路，确认事件来源。
- [x] 2. 定义聊天 trace event schema 与脱敏策略。
- [x] 3. 后端从 LangGraph stream updates/messages 中提取工具调用与工具结果。
- [x] 4. `/api/chat` 返回非流式 trace，`/api/chat_stream` 推送 trace SSE。
- [x] 5. 前端聊天气泡增加“执行轨迹”折叠面板。
- [x] 6. 补充测试、构建并更新过程记录。
