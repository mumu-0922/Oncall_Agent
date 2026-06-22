# 高级 AI 工程化面试缺口审计

> 目标：把 `Oncall_Agent` 从“会跑 Agent/RAG demo”升级成面试可追问、可验证、可量化的 AI 工程化项目。
> 参考：`/home/mumu/projects/GenericAgent` 的极简 Agent Loop、层级记忆、自我沉淀、评测叙事。

## 1. 当前任务定义

吾本轮任务不是继续泛泛学习，而是：

1. 审计当前 `Oncall_Agent` 项目能支撑哪些高级 AI 工程化面试问题。
2. 对照候选人常见短板：只会框架、不懂原理、无量化、无踩坑。
3. 参考 `GenericAgent`，提炼可借鉴设计，但不照搬其通用电脑控制形态。
4. 缺什么补什么：优先补“面试能展示的证据材料、评估脚本、话术结构”。

## 2. 你当前已经能讲的硬点

| 能力 | 项目证据 | 面试讲法 |
|---|---|---|
| RAG 入库链路 | `app/api/file.py` → `vector_index_service.py` → `document_splitter_service.py` → `vector_store_manager.py` | 文档上传后按 Markdown 结构和递归切分生成 chunk，再 embedding 写入 Milvus。 |
| Agent 工具调用 | `app/services/rag_agent_service.py` 使用 `create_agent`，本地工具含 `retrieve_knowledge`、`get_current_time` | 用户问题不是直接问 LLM，而是由 Agent 决定是否调用知识库或时间工具。 |
| 长对话压缩 | `SummarizationMiddleware` + `MemorySaver` | 长会话通过摘要保留目标、约束、工具结果，减少 token 和上下文噪声。 |
| AIOps workflow | `app/services/aiops_service.py` + `app/agent/aiops/*` | 用 Plan-Execute-Replan 拆解诊断任务，避免单轮 prompt 直接编报告。 |
| MCP 工具接入 | `app/agent/mcp_client.py` + `mcp_servers/*` | 日志和监控作为外部工具接入 Agent，符合真实 OnCall 排障链路。 |
| 基础测试 | `tests/test_rag_agent_service.py` | 已测试摘要消息过滤、长历史摘要、query 只发送最新用户输入。 |

## 3. 当前最容易被追杀的缺口

### P0：没有量化评估闭环

现状：项目能运行 RAG，但没有检索评估、回答评估、诊断评估。

面试官会问：

- chunk 大小为什么是 800？
- top_k 为什么是 3？
- 加不加 reranker 对结果有什么影响？
- 这个 Agent 诊断准确率是多少？

补强动作：

- 已新增 `evals/golden_cases.json`：8 条 OnCall golden cases。
- 已新增 `scripts/eval_retrieval.py`：可离线跑 retrieval benchmark，输出 hit_rate、Recall@K、Precision@K、MRR、latency。
- 下一步应接入真实 Milvus provider 或在服务启动后跑 `--provider app`。

面试话术：

> 我没有只说“用了向量库”，而是为 RAG 建了一套 golden cases。每条 case 标注 expected source 和 answer points。先用 Recall@K/MRR 评估检索，再用 answer point coverage 或 LLM-as-Judge 评估生成质量。这样 chunk/top_k/rerank 都能做 A/B 对比，而不是靠感觉调参。

### P1：RAG 仍是单路 dense retrieval

现状：`retrieve_knowledge` 当前只做 `vector_store.as_retriever(k=rag_top_k)`。

风险：遇到精确错误码、服务名、日志关键字时，纯向量可能不如 BM25；没有 reranker 时 top_k 小会错过关键 chunk。

建议补法：

1. 初召回从 `top_k=3` 改成 `fetch_k=10/20`。
2. 增加 reranker：输入 `query + candidate chunks`，输出 `score + sorted chunks`。
3. 增加 hybrid search：BM25 负责关键词，dense 负责语义。
4. 用 `scripts/eval_retrieval.py` 扩展 provider，做 `dense` vs `dense+rerank` vs `hybrid` 对比。

面试话术：

> dense retrieval 适合语义相似，但 OnCall 文档里服务名、错误码、指标名非常关键，所以我会用 dense 做语义召回，用 BM25 保住关键词召回，再用 reranker 对 query-doc pair 重新打分。reranker 的输入是用户 query 和初召回文档片段，输出是每个候选 chunk 的相关性分数，最后取 top_n 注入上下文。

### P1：AIOps 证据链不够硬

现状：最终报告 prompt 要求“基于真实数据”，但工程上没有 evidence ledger 约束。

建议补法：

- 每次工具调用记录：`evidence_id`、tool name、input、output digest、latency、status。
- 最终报告每个结论必须引用 evidence id。
- 如果证据不足，输出“无法确认”而不是编造根因。

借鉴 GenericAgent：

- GA 的 agent loop 每轮会显式记录 tool calls 和 tool results；这比只看最终回答更适合 debug。
- OnCall Agent 应把这种“工具执行轨迹”产品化为诊断证据表。

面试话术：

> AIOps Agent 最怕“像专家但没证据”。我会把工具调用结果沉淀成 evidence ledger，最终报告的每个根因必须引用日志或指标证据。如果只有 CPU 曲线没有日志证据，就只能给假设和下一步验证动作，不能直接下结论。

### P1：Mock 数据随机且不可回放

现状：`mcp_servers/monitor_server.py` 使用随机波动生成监控数据，CLS/Monitor README 也写明当前是模拟数据。

风险：面试 demo 不稳定；评估不可复现。

建议补法：

- MCP 工具增加 `incident_id` 参数。
- 每个 incident 固定返回指标、日志、历史工单。
- `evals/incidents/*.json` 存标准根因和证据。

面试话术：

> 早期 mock 只是为了打通链路。为了做评估，我会把 mock 改成可回放 incident fixture：同一个 incident_id 每次返回同样的日志和指标，这样才能衡量 Agent 的诊断稳定性和改造前后效果。

### P2：Prompt 和策略没有版本化

现状：planner、executor、replanner prompt 写在代码里，缺少独立版本、变更记录和 A/B。

借鉴 GenericAgent：

- GA 把 SOP、memory、tool schema 和 agent loop 分层，能力沉淀在可读文本资产里。
- OnCall Agent 也应把 prompt 当工程资产管理。

建议补法：

- 建 `prompts/aiops/planner_v1.md`、`executor_v1.md`、`replanner_v1.md`。
- 每次 prompt 改动必须跑 golden cases。
- 面试展示 prompt 不是“玄学调参”，而是版本化实验。

### P2：缺少成本、延迟、工具成功率监控

现状：日志有，但没有统一 metrics。

借鉴 GenericAgent：

- `frontends/cost_tracker.py` 追踪 requests/input/output/cache tokens。
- OnCall Agent 应至少记录 token、latency、tool_success_rate、retrieval_latency。

建议补法：

- 增加 `app/services/telemetry_service.py` 或简单 middleware。
- 每次 LLM 调用记录 model、latency、token usage（如果 provider 返回）。
- 每次 tool 调用记录 status 和耗时。

### P2：安全与生产边界需要能主动承认

现状：CORS `allow_origins=["*"]`，`.env` 出现在工作区改动中，上传接口只有扩展名和大小校验。

面试风险：高级工程化岗位会看安全意识。

建议话术：

> 当前是本地 demo 配置，生产化要收紧 CORS、鉴权、上传内容扫描、路径隔离、接口限流、日志脱敏。RAG 文档和工具返回都属于 untrusted context，不能让它们覆盖系统指令。

## 4. GenericAgent 对你项目最有价值的三个借鉴

### 4.1 极简核心循环，而不是框架堆叠

GenericAgent 的核心卖点是“9 atomic tools + ~100 行 loop”。它能讲清每轮：LLM → tool call → tool result → next prompt → exit。

你的 OnCall Agent 应补一张简化流程图，讲清：

```text
用户问题 / 告警任务
  → Planner 生成证据采集计划
  → Executor 调 MCP/RAG 工具
  → Evidence Ledger 记录证据
  → Replanner 判断信息是否足够
  → Reporter 生成带引用报告
  → Eval 对比 golden case
```

### 4.2 记忆/经验沉淀，不只是会话上下文

GA 有 L0-L4 memory 和 SOP crystallization。你的项目目前只有会话 MemorySaver + 摘要。

OnCall 场景可转化为：

- L0：系统安全和不编造规则。
- L1：告警类型索引，如 CPU、内存、磁盘、慢响应。
- L2：服务拓扑、Runbook、指标口径。
- L3：历史 incident SOP 和复盘结论。
- L4：原始诊断会话归档。

面试话术：

> 我区分 conversation memory 和 operational memory。前者服务当前对话，后者沉淀可复用排障经验。真正的 OnCall Agent 不能每次从零开始查，应该能把已验证的 incident 复盘固化为 SOP，并在下次相似告警时召回。

### 4.3 评测叙事强于功能罗列

GA README 明确按五个维度评测：任务完成、工具效率、记忆效果、自进化、浏览能力。

你的项目可以按四个维度评测：

1. RAG Retrieval：Recall@K、MRR、NDCG。
2. Answer Quality：answer point coverage、faithfulness。
3. Diagnosis Quality：root cause accuracy、evidence completeness。
4. Engineering Efficiency：latency、token cost、tool success rate。

## 5. 面试还缺什么：优先级清单

| 优先级 | 缺口 | 为什么重要 | 补法 |
|---|---|---|---|
| P0 | RAG 量化评估 | 防止被问倒 Recall@5/reranker | 已补 `evals/golden_cases.json` + `scripts/eval_retrieval.py` |
| P0 | 一份项目架构讲稿 | 面试需要 30s/2min/10min 三档表达 | 写入本文件第 6 节，可继续抽成 `docs/interview_pitch.md` |
| P1 | evidence ledger | 防止 AIOps 报告像编造 | 增加工具调用轨迹结构和报告引用 |
| P1 | 可回放 incident fixtures | 诊断评估必须稳定 | 固定 mock 数据，去 random，增加 incident_id |
| P1 | reranker/hybrid 对比 | 从“会用向量库”进入 RAG 深水区 | 在评估脚本中加入 variant 对比 |
| P2 | token/latency/cost 观测 | 高级工程化必须讲成本 | 仿 GA cost_tracker，记录 LLM/tool metrics |
| P2 | Prompt 版本化 | 防止 prompt 散落代码不可控 | 拆出 `prompts/` 并绑定评估 |
| P2 | 更多测试 | 当前覆盖率很低 | 补 file upload、splitter、retrieval formatting、aiops event 测试 |

## 6. 面试表达骨架

### 30 秒版

> 我做的是一个面向企业 OnCall 场景的 AI Agent 系统，核心是 RAG 知识库问答和 AIOps 自动诊断。文档会被切分、embedding 后写入 Milvus；用户问题或告警任务进来后，Agent 通过 LangGraph 编排计划、执行、重规划，并通过 MCP 调日志和监控工具。为了避免只做 demo，我补了 golden cases 和检索评估脚本，用 Recall@K、MRR、延迟来衡量 RAG 效果，后续会把工具调用记录成 evidence ledger，保证诊断报告可追溯。

### 2 分钟版

> 这个项目分两条主链。第一条是 RAG：文件上传后，系统根据 Markdown 标题和递归切分生成 chunk，调用 DashScope embedding，写入 Milvus。问答时，Agent 根据用户问题决定是否调用 `retrieve_knowledge`，把检索结果格式化为上下文再生成回答。这里我重点关注 chunk、top_k、rerank 的效果，所以新增了 golden cases 和 retrieval benchmark。
>
> 第二条是 AIOps：不是单轮 prompt 让模型编报告，而是 Plan-Execute-Replan。Planner 先基于告警任务和知识库 runbook 生成排障计划，Executor 调 MCP 工具查询监控、日志、服务信息，Replanner 判断信息是否足够，最后生成诊断报告。这个设计的价值是把大模型的不确定性拆成可观察步骤。
>
> 目前短板我也明确：MCP 还是 mock 数据，纯 dense retrieval，没有 reranker/hybrid，没有完整 evidence ledger，测试覆盖不足。我的补强方向是把 mock 变成可回放 incident，把每次工具调用形成证据链，然后用 retrieval/diagnosis golden cases 衡量改造效果。

### 10 分钟深挖版结构

1. 业务问题：OnCall 排障信息散落在文档、日志、监控、历史工单里。
2. 系统方案：RAG 召回经验 + MCP 查询实时证据 + LangGraph 管控流程。
3. RAG 深挖：chunk、embedding、Milvus、top_k、reranker/hybrid、评估指标。
4. Agent 深挖：Planner/Executor/Replanner 边界、工具输入输出、失败兜底。
5. 工程化：SSE、MemorySaver、摘要、MCP retry、测试、配置、部署。
6. 量化：golden cases、Recall@K、MRR、answer coverage、latency。
7. 短板：mock、评估少、安全边界、生产持久化。
8. 下一步：evidence ledger、incident replay、prompt 版本化、hybrid retrieval。

## 7. 高频追问与推荐回答

### Q1：embedding 和 chunking 有什么区别？

推荐答：

> chunking 是把文档切成可检索片段，解决“检索粒度”和上下文长度问题；embedding 是把 chunk 或 query 映射成向量，解决“语义相似度计算”问题。一个 chunk 通常对应一个 embedding，但二者不是一回事。chunk 过大召回不准，过小语义不完整，所以需要通过 golden cases 评估。

### Q2：为什么不是直接把所有文档塞进 prompt？

推荐答：

> 文档量大时直接塞会超上下文，也会引入噪声和成本。RAG 的核心是先召回少量相关片段，再让模型基于证据回答。OnCall 场景还要求引用来源，否则无法判断建议来自哪里。

### Q3：reranker 的输入输出是什么？

推荐答：

> 输入是用户 query 和初召回的一组候选 chunk，一般是 pair 列表 `[query, doc]`。输出是每个候选 chunk 的相关性分数或排序。它不负责生成答案，只负责把最相关的证据排到前面。

### Q4：Agent 什么时候会挂？

推荐答：

> 常见有四类：检索没召回关键证据、工具参数填错、工具返回错误或空数据、模型过早下结论。兜底上，我会做参数 schema 校验、工具 retry、evidence ledger、证据不足时输出假设而非结论，并用 golden incidents 回放测试稳定性。

### Q5：为什么用 Plan-Execute-Replan？

推荐答：

> AIOps 诊断天然是多步证据收集，不适合单轮 prompt。Plan 负责拆任务，Execute 负责拿证据，Replan 负责判断是否继续查或收敛报告。这样每一步都有可观察输出，失败也能定位是计划错、工具错还是模型判断错。

### Q6：MCP 和普通函数封装有什么区别？

推荐答：

> 普通函数封装只在本进程内好用。MCP 更像统一工具协议，可以把日志、监控、工单等能力作为独立 server 暴露给 Agent，便于跨语言、跨进程、权限隔离和复用。我的项目里 CLS 和 Monitor 就是两个 MCP server。

### Q7：这个项目你最想改的是什么？

推荐答：

> 第一是评估闭环，已经开始补 golden cases 和 retrieval metrics。第二是证据链，AIOps 报告必须引用工具证据。第三是可回放 incident，把随机 mock 改成固定事故库，这样才能做诊断准确率对比。

## 8. 不要在简历上乱写的词

除非已经补完并能解释，否则少写或别写：

- “三大 Agent”——项目实际是 workflow 节点，不是成熟多 Agent 平台。
- “生产级持久化记忆”——当前 `MemorySaver` 更偏单机内存态。
- “Recall@5 提升 xx%”——除非跑了 `scripts/eval_retrieval.py` 并保存报告。
- “reranker 优化”——当前主链还没有 reranker。
- “真实生产监控/日志接入”——当前 MCP README 明确是模拟数据。

## 9. 最近 1 天补强路线

1. 跑：`make test`，修 Makefile 使用虚拟环境 Python。
2. 跑：`.venv/bin/python scripts/eval_retrieval.py --provider local --k 3 --out evals/retrieval_local_report.json`。
3. 把报告里的 hit_rate、MRR 写进面试笔记。
4. 准备一张架构图：RAG 入库、问答、AIOps 诊断、评估闭环。
5. 口述 30s、2min、10min 三版，遇到不知道的就说“当前短板 + 下一步怎么量化验证”。

## 10. 最近 3 天补强路线

Day 1：评估闭环
- 完善 golden cases 到 20 条。
- 增加 app provider 真实 Milvus 检索评估。
- 对比 `top_k=3/5/10`。

Day 2：证据链
- 增加 `EvidenceRecord` 模型。
- Executor 每次工具调用记录 evidence。
- 最终报告引用 evidence id。

Day 3：可回放事故库
- 增加 `evals/incidents/*.json`。
- MCP 工具按 incident_id 返回固定数据。
- 写 diagnosis eval：根因命中、证据完整、建议可执行。
