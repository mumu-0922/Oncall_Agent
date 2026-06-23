# Auto OnCall 学习总结

## Day 1 总结：RAG 与对话链路

### 1. 项目是做什么的

这是一个面向企业 OnCall 场景的 AI Agent 系统，核心能力有两块：

- RAG 知识库问答
- AIOps 智能诊断

Day 1 先只吃透第一块，也就是：

- 文档怎么进知识库
- 用户问题怎么触发检索
- 对话怎么返回给前端
- 长对话怎么管理上下文

### 2. Day 1 必须讲顺的主链

#### 2.1 文档入库链

1. 用户上传文件
2. 后端读取文件内容
3. 文档被切成多个 chunk
4. 每个 chunk 做 embedding
5. 向量和文本一起写入 Milvus

对应文件：

- `app/api/file.py`
- `app/services/vector_index_service.py`
- `app/services/document_splitter_service.py`
- `app/services/vector_embedding_service.py`
- `app/services/vector_store_manager.py`

一句话记忆：

`上传文件 -> 切分 chunk -> embedding -> 写入 Milvus`

#### 2.2 用户问答链

1. 用户调用 `/chat` 或 `/chat_stream`
2. `chat.py` 把问题交给 `rag_agent_service`
3. Agent 判断要不要调用 `retrieve_knowledge`
4. `retrieve_knowledge` 去 Milvus 检索相关 chunk
5. 检索结果整理成上下文文本
6. 模型基于上下文生成答案
7. 普通接口一次性返回，流式接口通过 SSE 一段段返回

对应文件：

- `app/api/chat.py`
- `app/services/rag_agent_service.py`
- `app/tools/knowledge_tool.py`

一句话记忆：

`用户提问 -> Agent 调工具检索 -> 检索结果给模型 -> 返回答案`

### 3. 关键模块怎么理解

#### 3.1 `document_splitter_service.py`

作用：把一篇长文档切成多个小块 `chunk`。

为什么要切：

- 一整篇太长，不方便检索
- 向量检索通常按片段匹配，不按整篇匹配
- 小块更容易找到真正相关的内容

要记住：

- `chunk` 就是一小段文本
- 通常一个 `chunk` 对应一个 `embedding`

#### 3.2 `vector_embedding_service.py`

作用：把文本变成向量。

白话：

- 文本人能看懂
- 向量数据库更擅长比较“语义像不像”
- 所以要先把文本和问题都变成向量

#### 3.3 `vector_store_manager.py`

作用：封装 Milvus 的操作。

你要会说的 3 个点：

- `add_documents()`：把 chunks 写入 Milvus
- `similarity_search()`：按语义查相似内容
- `get_vector_store()`：拿到底层向量库对象

Milvus 里存的不是整篇文件，而是很多条 chunk 记录，每条通常包括：

- `content`：文本内容
- `vector`：向量
- `id`：唯一编号
- `metadata`：来源文件、标题等附加信息

#### 3.4 `knowledge_tool.py`

作用：把“知识库检索”封装成 Agent 可调用工具。

主链：

`vector_store -> retriever -> invoke(query) -> docs -> format_docs(docs) -> context`

白话：

- `vector_store`：知识库对象
- `retriever`：检索器，相当于帮你查库的人
- `invoke(query)`：拿问题发起检索
- `docs`：检索出来的原始文档结果
- `format_docs()`：把结果整理成模型更容易读的上下文文本

为什么返回 `context, docs` 两个值：

- `context` 给模型看
- `docs` 给系统留着做来源追踪和后续处理

#### 3.5 `rag_agent_service.py`

这是 Day 1 最重要的文件。

它主要做 4 件事：

1. 初始化模型、prompt、工具、memory
2. 异步加载 MCP 工具并创建 Agent
3. 处理非流式问答 `query`
4. 处理流式问答 `query_stream`

你要会讲：

- `__init__`：先准备好模型、摘要模型、本地工具、会话记忆
- `_initialize_agent`：把本地工具 + MCP 工具 + prompt + memory 组装成真正可用的 Agent
- `query`：一次性等完整答案
- `query_stream`：一边生成一边往外吐内容

### 4. 会话管理和摘要机制

#### 4.1 `thread_id` 和 `MemorySaver`

作用：管理多轮对话上下文。

白话理解：

- `thread_id` 就是会话编号
- `MemorySaver` 就是会话记忆本
- 只要用户继续带同一个 `session_id/thread_id`，系统就知道这是同一场聊天

所以：

- 不用手动把所有历史消息一条条重新拼到 prompt
- 框架会根据 `thread_id` 自动恢复上下文

#### 4.2 `SummarizationMiddleware`

作用：在长对话场景下压缩历史消息，避免上下文越来越长。

项目里已经真实接入，且默认开启。

当前配置：

- 达到 `12` 条消息左右触发摘要
- 保留最近 `6` 条原始消息
- 更早的历史做摘要压缩

为什么需要摘要：

- 降低 token 成本
- 降低长上下文噪声
- 提升长对话稳定性

一句话记忆：

- `MemorySaver` 负责记住历史
- `SummarizationMiddleware` 负责把历史变短

### 5. SSE 流式输出怎么理解

SSE 的最简单理解：

后端不断发，前端持续收，所以页面能看到字一点点出来。

要分清 3 层：

- `astream`：模型层，负责持续产出内容
- `yield`：Python 层，负责把产出的内容一段段往上交
- `SSE`：HTTP 传输层，负责把这些内容实时发给浏览器

所以：

- `query()` 是整段返回
- `query_stream()` 是边生成边返回
- `/chat_stream` 用 `EventSourceResponse` 把内容转成 SSE 发给前端

一句话记忆：

`astream` 负责产出，`yield` 负责往上送，`SSE` 负责往前端发。

### 6. Day 1 你至少要能脱口而出的 8 句话

1. 这是一个面向企业 OnCall 场景的 AI Agent 系统，核心能力是 RAG 问答和 AIOps 智能诊断。
2. 聊天入口在 `/chat` 和 `/chat_stream`。
3. 文档入库链路是：上传文件 -> 切分 chunk -> embedding -> 写入 Milvus。
4. Milvus 是向量数据库，用来做语义检索，不是大模型。
5. `retrieve_knowledge` 是 Agent 可调用的知识检索工具。
6. 非流式问答用 `ainvoke()`，流式问答用 `astream()`。
7. `MemorySaver` 负责会话记忆，`thread_id` 负责会话隔离。
8. `SummarizationMiddleware` 用来压缩长对话历史，降低 token 和噪声。

## Day 1 高频面试问答

### 1. 这个项目是做什么的？

答：

这是一个面向企业 OnCall 场景的 AI Agent 系统，核心能力包括 RAG 知识库问答和 AIOps 智能诊断。RAG 部分负责基于企业内部文档做智能问答，对话部分支持多轮上下文和 SSE 流式交互，AIOps 部分负责结合知识库和工具做智能排障。

### 2. RAG 链路怎么走？

答：

RAG 分两段。第一段是离线入库：上传文档、切分 chunk、生成 embedding、写入 Milvus。第二段是在线问答：用户提问后，Agent 判断是否需要检索知识库，`retrieve_knowledge` 工具会从 Milvus 中召回相关 chunk，并将它们整理成上下文文本提供给模型，最终生成答案。

### 3. 为什么要做 RAG，而不是直接问模型？

答：

因为通用模型不知道企业内部文档内容，直接问容易答非所问或者幻觉。RAG 的作用是先检索内部知识，再把相关内容作为上下文给模型，从而提升答案准确性和可解释性。

### 4. 为什么要切 chunk？

答：

因为整篇文档太长，不适合直接做检索。切成 chunk 后，向量库可以更精确地找到和用户问题最相关的片段，也更适合后续作为上下文提供给模型。

### 5. 为什么用 Milvus？

答：

因为这个场景需要做语义检索，不是关键词精确匹配。用户问题和文档原文往往表述不完全一样，但语义接近，所以要用向量数据库存储 embedding，并做相似度搜索。Milvus 就是这个项目的向量检索底座。

### 6. `retrieve_knowledge` 是怎么工作的？

答：

它是一个注册给 Agent 的工具。Agent 觉得问题需要知识库时，会调用它。工具内部先拿到向量库对象，再构造 retriever，通过 `invoke(query)` 发起相似度检索，拿到相关 `docs` 后再通过 `format_docs()` 整理成结构化上下文文本，供模型生成答案。

### 7. 为什么 `retrieve_knowledge` 返回两个值？

答：

因为 `context` 是给模型看的整理后文本，而 `docs` 是原始文档对象，方便系统保留来源信息、做调试或后续展示。一个给模型用，一个给系统用。

### 8. `/chat` 和 `/chat_stream` 的区别是什么？

答：

- `/chat`：非流式，等 Agent 完整执行结束后，一次性返回完整答案
- `/chat_stream`：流式，后端边生成边返回，前端实时显示

### 9. SSE 是什么？为什么适合这个项目？

答：

SSE 是服务器持续向前端推送消息的方式。在这个项目里，大模型回答不是瞬间完成，所以适合用 SSE 实现流式输出。这样用户不用等整段答案都生成完，就能先看到前面的内容，交互体验更好。

### 10. `astream` 和 `yield` 分别负责什么？

答：

- `astream`：从 Agent/模型那里持续拿流式输出
- `yield`：把拿到的每一小段内容立刻返回给上一层

一句话：

`astream` 负责产出，`yield` 负责转发。

### 11. `query_stream()` 返回的是什么？

答：

它返回的不是完整字符串，而是一个异步生成器。这个生成器会不断产出小块字典数据，比如 `content`、`complete`、`error`，然后由接口层包装成 SSE 发给前端。

### 12. `thread_id` 是干嘛的？

答：

它是会话标识。相同的 `thread_id` 代表同一场对话，框架会基于它自动恢复上下文历史，从而支持多轮对话。

### 13. 有了 `MemorySaver`，为什么还要 `SummarizationMiddleware`？

答：

`MemorySaver` 负责记住历史，但历史会越来越长。长对话会带来 token 成本、延迟和上下文噪声问题，所以需要 `SummarizationMiddleware` 对更早的历史做压缩，只保留关键事实，同时保留最近几轮原始消息。

### 14. `RagAgentService.__init__` 在做什么？

答：

它不是在回答问题，而是在准备运行环境，包括主模型、摘要模型、系统提示词、本地工具、会话记忆和初始化状态。真正的 Agent 创建会在后续异步初始化方法里完成。

### 15. `_initialize_agent()` 在做什么？

答：

它会异步获取 MCP 工具，并和本地工具合并，然后通过 `create_agent()` 把模型、工具、prompt、middleware、memory 组装成真正可执行的 Agent。

## Day 1 口述模板

### 1 分钟版本

这个项目是一个面向企业 OnCall 场景的 AI Agent 系统，Day 1 我主要学习了 RAG 和对话链路。知识库部分的流程是上传文档后，先切分成多个 chunk，再做 embedding，最后写入 Milvus。用户提问时，会通过 `/chat` 或 `/chat_stream` 进入系统，`rag_agent_service` 会调用 Agent，Agent 根据问题决定是否使用 `retrieve_knowledge` 工具去 Milvus 里检索相关知识，再把检索结果整理成上下文交给模型生成答案。系统还用了 `MemorySaver` 做会话管理，用 `SummarizationMiddleware` 处理长对话摘要，并通过 SSE 实现流式返回。

### 3 分钟版本骨架

你按这个顺序讲：

1. 项目定位
   这是一个企业 OnCall AI Agent 系统，核心有 RAG 问答和 AIOps 诊断。

2. 文档入库
   文档上传后，服务会读取文件内容，按标题和大小切成多个 chunk，再对每个 chunk 做 embedding，最后把文本、向量和 metadata 一起写入 Milvus。

3. 用户问答
   用户通过 `/chat` 或 `/chat_stream` 提问，问题进入 `rag_agent_service`。这里会先初始化 Agent，然后把问题封装成 `HumanMessage`，并带上 `thread_id`。Agent 会根据问题决定是否调用 `retrieve_knowledge` 工具，工具内部通过 retriever 从 Milvus 检索相关 chunk，再整理成上下文文本给模型，最后生成答案。

4. 流式交互
   非流式接口通过 `ainvoke()` 一次性拿完整答案。流式接口通过 `astream()` 持续拿输出，再通过 `yield` 一段段向上返回，最后用 SSE 发给前端，所以页面可以看到打字机效果。

5. 会话与摘要
   项目用 `MemorySaver` + `thread_id` 管理多轮对话，用 `SummarizationMiddleware` 压缩长历史，避免 token 成本和长上下文噪声问题。

## Day 1 剩余检查清单

如果下面这些你都能脱口而出，Day 1 就算过：

- 什么是 chunk
- 什么是 embedding
- Milvus 存的是什么
- `retrieve_knowledge` 怎么工作
- `/chat` 和 `/chat_stream` 的区别
- `ainvoke` 和 `astream` 的区别
- `thread_id` 和 `MemorySaver` 的作用
- 为什么需要 `SummarizationMiddleware`
