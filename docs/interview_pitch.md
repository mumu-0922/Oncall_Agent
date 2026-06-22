# OnCall Agent 面试速记

## 一句话定位

面向企业 OnCall 的 AI Agent：用 RAG 召回排障经验，用 MCP 查询日志/监控实时证据，用 LangGraph 管控 Plan-Execute-Replan 诊断流程。

## 30 秒介绍

这是一个企业 OnCall 场景的 AI Agent 系统，核心能力是 RAG 知识库问答和 AIOps 自动诊断。文档上传后会切分、embedding 并写入 Milvus；用户问题或告警任务进来后，Agent 通过 LangGraph 规划、执行、重规划，并通过 MCP 调日志和监控工具。为了避免停留在 demo，我补了 golden cases 和检索评估脚本，用 Recall@K、MRR、延迟衡量 RAG 效果。

## 技术主链

```text
文档上传
  → Markdown/Recursive chunking
  → DashScope embedding
  → Milvus collection
  → retrieve_knowledge tool
  → Agent 注入上下文生成回答

AIOps 诊断
  → Planner 制定排障步骤
  → Executor 调 MCP/RAG 工具
  → Replanner 判断继续/收敛
  → Reporter 输出诊断报告
```

## 当前项目亮点

- 有完整 RAG 入库和检索链路。
- 有 LangGraph workflow，不是单轮 prompt。
- 有 MCP 日志/监控工具接入。
- 有 SSE 流式输出。
- 有 `MemorySaver` + `SummarizationMiddleware` 管理长对话。
- 已开始补 golden cases 和 retrieval benchmark。

## 当前短板，主动承认

- MCP 仍是 mock 数据，生产要接真实日志/监控。
- 当前检索是 dense top_k，没有 hybrid/reranker。
- 没有完整 evidence ledger，诊断报告证据引用还不够硬。
- 测试覆盖低，主要覆盖了 RAG memory 相关逻辑。
- prompt 还写在代码里，后续要版本化和 A/B。

## 面试时必须说清的指标

- Retrieval：Hit Rate、Recall@K、Precision@K、MRR。
- Generation：answer point coverage、faithfulness。
- AIOps：root cause accuracy、evidence completeness。
- 工程：latency、token cost、tool success rate。

## 一句高级表达

我不是把 LangChain、LangGraph、Milvus 拼起来就结束，而是把 Agent 的不确定性拆成可观察、可评估、可回放的工程链路：检索有 golden cases，工具调用有证据记录，诊断结论要能追溯到日志和指标。
