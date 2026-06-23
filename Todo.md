# Auto OnCall 面试冲刺 Todo

目标：用 3 天时间把 `Auto OnCall` 项目学到可以支撑 `AI 大模型开发` 岗技术面。

原则：
- 不追求学完整个仓库，只追求能讲清简历上的每一句。
- 每学完一个模块，必须脱稿讲一遍：`是什么 -> 为什么 -> 怎么做 -> 短板`。
- 每天结束前都要做一次 10 到 15 分钟口述模拟。

---

## Day 1：RAG 与对话链路

### 上午：建立项目全局图
- [ ] 阅读 `README.md`
- [ ] 阅读 `app/main.py`
- [ ] 阅读 `app/config.py`
- [ ] 阅读 `app/api/chat.py`
- [ ] 阅读 `app/models/request.py`

目标：
- [ ] 讲清项目做什么
- [ ] 讲清核心接口有哪些
- [ ] 讲清聊天请求是怎么进入系统的

你要能说的话：
- [ ] 这是一个面向企业 OnCall 场景的 AI Agent 系统
- [ ] 核心能力是 RAG 问答和 AIOps 智能诊断
- [ ] 聊天入口在 `/api/chat` 和 `/api/chat_stream`

### 下午：吃透 RAG 主链
- [ ] 阅读 `app/api/file.py`
- [ ] 阅读 `app/services/vector_index_service.py`
- [ ] 阅读 `app/services/document_splitter_service.py`
- [ ] 阅读 `app/services/vector_embedding_service.py`
- [ ] 阅读 `app/services/vector_store_manager.py`
- [ ] 阅读 `app/tools/knowledge_tool.py`
- [ ] 阅读 `app/services/rag_agent_service.py`

目标：
- [ ] 讲清文档如何上传并进入 Milvus
- [ ] 讲清用户问题如何触发检索并生成答案

必须讲顺的链路：
1. 用户上传文档
2. 文档被切分
3. 文本做 embedding
4. 向量写入 Milvus
5. 用户提问时调用 `retrieve_knowledge`
6. 检索结果作为上下文交给模型生成答案

### 晚上：摘要与会话管理
- [ ] 再读 `app/services/rag_agent_service.py`
- [ ] 重点看 `SummarizationMiddleware` 接入
- [ ] 重点看 `thread_id`、`MemorySaver`、`get_session_history`

目标：
- [ ] 讲清为什么需要上下文摘要
- [ ] 讲清会话隔离怎么做
- [ ] 讲清 SSE 流式输出有什么价值

当天口述题：
- [ ] RAG 链路怎么走？
- [ ] 为什么需要对话摘要机制？
- [ ] 为什么要用 SSE？

---

## Day 2：AIOps Workflow 与 MCP Tool Calling

### 上午：吃透 Plan-Execute-Replan
- [ ] 阅读 `app/api/aiops.py`
- [ ] 阅读 `app/services/aiops_service.py`
- [ ] 阅读 `app/agent/aiops/state.py`
- [ ] 阅读 `app/agent/aiops/planner.py`
- [ ] 阅读 `app/agent/aiops/executor.py`
- [ ] 阅读 `app/agent/aiops/replanner.py`

目标：
- [ ] 讲清为什么不是单轮 prompt，而是 workflow
- [ ] 讲清 planner、executor、replanner 各自负责什么

必须讲顺的链路：
1. 用户发起 AIOps 请求
2. Planner 先制定排障步骤
3. Executor 逐步调用工具查询信息
4. Replanner 决定继续执行还是直接生成报告
5. 最终输出诊断建议

### 下午：吃透 MCP 接入
- [ ] 阅读 `app/agent/mcp_client.py`
- [ ] 阅读 `mcp_servers/README.md`
- [ ] 阅读 `mcp_servers/cls_server.py`
- [ ] 阅读 `mcp_servers/monitor_server.py`

目标：
- [ ] 讲清 MCP 是什么
- [ ] 讲清这个项目里 MCP 提供了什么能力
- [ ] 讲清 Agent 如何通过 MCP 工具查日志和监控

必须讲顺的点：
- [ ] `mcp_client` 负责统一获取工具
- [ ] `CLS Server` 提供日志查询能力
- [ ] `Monitor Server` 提供监控与服务信息查询能力
- [ ] 工具被 Agent 作为可调用能力使用

### 晚上：结合知识库样例理解场景
- [ ] 阅读 `aiops-docs/service_unavailable.md`
- [ ] 阅读 `aiops-docs/cpu_high_usage.md`
- [ ] 阅读 `aiops-docs/disk_high_usage.md`

目标：
- [ ] 讲清 AIOps 不是空泛聊天，而是“知识库 + 工具查询 + workflow”
- [ ] 讲清知识库文档在诊断中的作用

当天口述题：
- [ ] 为什么使用 LangGraph？
- [ ] MCP 在这个项目里的价值是什么？
- [ ] AIOps 诊断链路怎么走？

---

## Day 3：面试化表达与高频追问

### 上午：整理简历话术
- [ ] 把项目经历最终版过一遍
- [ ] 删掉答不住的词：`三大agent`、`持久化存储`、无证据量化指标
- [ ] 准备 30 秒项目介绍
- [ ] 准备 90 秒技术方案介绍

你要能背下的 30 秒版本：
- [ ] 这是一个面向企业 OnCall 场景的 AI Agent 系统，核心能力是 RAG 问答和 AIOps 诊断，底层通过 LangGraph 编排工作流，通过 MCP 接入日志与监控工具，并通过 SSE 提供流式交互。

### 下午：准备高频技术问答
- [ ] 为什么要做 RAG，而不是直接问模型？
- [ ] 文档切分为什么这样设计？
- [ ] 为什么选 Milvus？
- [ ] 为什么使用 LangGraph？
- [ ] Tool Calling 在这个项目里怎么落地？
- [ ] MCP 和普通函数封装相比有什么价值？
- [ ] SSE 为什么适合这个场景？
- [ ] 当前系统的短板是什么？

每题都按这个结构答：
- [ ] 背景问题
- [ ] 当前方案
- [ ] 这样设计的原因
- [ ] 短板和可改进点

### 晚上：模拟面试
- [ ] 进行 1 次 30 秒自我介绍
- [ ] 进行 1 次 90 秒项目介绍
- [ ] 进行 1 次 10 分钟项目深挖

模拟时至少覆盖：
- [ ] RAG 全链路
- [ ] 上下文摘要机制
- [ ] 会话隔离
- [ ] SSE 流式输出
- [ ] Plan-Execute-Replan
- [ ] MCP Tool Calling
- [ ] 项目短板

---

## 必须掌握的项目短板

- [ ] 当前会话状态基于 `MemorySaver`，更偏单机内存态，不是完整生产级持久化
- [ ] MCP Server 当前有较多 mock/示例数据，真实生产环境还需要对接实际数据源
- [ ] 测试覆盖不完整，项目整体自动化验证仍有提升空间
- [ ] 当前更多是 LLM 应用工程闭环，不是成熟的 Multi-Agent 平台

---

## 面试前最终验收

如果下面问题都能脱稿回答，就可以去面：

- [ ] 用 1 分钟讲清项目整体架构
- [ ] 用 2 分钟讲清 RAG 问答链路
- [ ] 用 2 分钟讲清 AIOps 诊断链路
- [ ] 讲清为什么要用 LangGraph
- [ ] 讲清为什么要接入 MCP
- [ ] 讲清上下文摘要机制解决什么问题
- [ ] 讲清项目目前最大的 3 个短板

最终目标：
- [ ] 简历上的每一句都能答三层
- [ ] 被追问时不靠猜，靠代码与设计逻辑回答
