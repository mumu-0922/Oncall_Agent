# AIOps 执行轨迹可视化改造计划

## 目标

让右上角 `AI Ops` 诊断链路能像普通聊天一样展示可审计运行轨迹：Planner/Replanner 阶段、Executor 步骤、真实工具调用、工具参数摘要、工具返回摘要。借鉴 GenericAgent 的 `LLM Running -> Tool args -> Tool output` 展示方式，但不暴露模型隐藏 chain-of-thought。

## 边界

- 只展示“思考摘要 / 执行轨迹 / 工具证据”，不展示原始 CoT。
- 工具参数和结果复用 `ChatTraceObserver` 的脱敏、截断策略。
- AIOps 继续保持 strict runtime：MCP、tool calling、structured output 失败要显式报错，不做假降级。
- 前端复用已有 `.agent-trace` 折叠面板，避免再造一套 UI。

## 改造步骤

- [x] 1. 定位 AI Ops 与普通聊天链路差异。
- [x] 2. 扩展 AIOps state，支持节点产出 `tool_events`。
- [x] 3. Executor 捕获 tool_call / tool_result / step stage 轨迹。
- [x] 4. AIOpsService 将节点轨迹转成 SSE `type=trace`。
- [x] 5. 前端 `sendAIOpsRequest()` 消费 trace 并渲染折叠面板。
- [x] 6. 补充测试、构建验证并更新过程记录。
