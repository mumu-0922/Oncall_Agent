# Model Provider 与 Embedding 降级改造计划

> 目标：参考 `nanobot` 的 Provider Registry / Factory 思路，把 Oncall_Agent 从 DashScope 强绑定改成 OpenAI-compatible 可配置模型接入，并在没有 embedding key 时支持 BM25-only RAG 降级。  
> 执行纪律：每完成一步，在本文 checklist 打钩，并在 `docs/model_provider_process.md` 追加过程记录。

## 0. nanobot 参考结论

`/home/mumu/projects/nanobot` 的模型接入核心不是在业务代码里到处写 `if provider == ...`，而是：

```text
ProviderSpec registry
  -> ProviderConfig / ModelPresetConfig
  -> factory.make_provider()
  -> OpenAI-compatible / native backend
  -> optional fallback provider chain
```

可借鉴点：

- Provider 元数据集中管理：名称、默认 base_url、key 来源、是否 gateway/local、匹配策略。
- 业务代码只依赖 Factory，不直接绑定具体厂商 SDK。
- `custom` / gateway 可以覆盖绝大多数 OpenAI-compatible 中转模型。

不能照搬点：

- `nanobot` 主要是 Agent Chat LLM provider，不是 RAG embedding 项目。
- 仓库里没有完整 embedding/RAG 索引模块，embedding 问题需要本项目自己解决。

## 1. 当前病灶

- `rag_agent_service.py` 和 AIOps planner/executor/replanner 仍直接使用 `ChatQwen` + `DASHSCOPE_API_KEY`。
- `vector_embedding_service.py` import 时创建 DashScope embedding，且 API key 缺失会直接报错。
- 上传文档时 dense embedding 失败会导致索引链路失败，无法只用 BM25/docstore 继续演示。
- FastAPI 启动默认连接 Milvus + VectorStore；没有 embedding / Milvus 时，纯 BM25 RAG 也被牵连。

## 2. 目标链路

### Chat LLM

```text
.env
  -> LLM_PROVIDER / LLM_API_BASE / LLM_API_KEY / LLM_MODEL
  -> app.core.model_provider_registry
  -> app.core.llm_factory.create_chat_model()
  -> RagAgentService / AIOps
```

### Embedding / RAG

```text
EMBEDDING_PROVIDER=dashscope|openai_compatible|disabled

文档上传
  -> parent-child split
  -> docstore upsert
  -> BM25 rebuild
  -> if embedding enabled: Milvus dense index
     else: dense skipped, upload/index still success

查询 hybrid_parent
  -> dense recall 尝试，不可用则空 recall
  -> BM25 recall
  -> rerank
  -> parent expansion
```

## 3. 配置目标

### GPT 中转 + 暂无 embedding（当前推荐）

```env
LLM_PROVIDER=openai_compatible
LLM_API_BASE=https://你的中转地址/v1
LLM_API_KEY=你的中转key
LLM_MODEL=你的模型名

EMBEDDING_PROVIDER=disabled
RAG_RETRIEVAL_MODE=hybrid_parent
```

### DashScope 全家桶

```env
LLM_PROVIDER=dashscope
LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=你的 DashScope key
LLM_MODEL=qwen-max

EMBEDDING_PROVIDER=dashscope
EMBEDDING_API_KEY=你的 DashScope key
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
```

### OpenAI-compatible embedding

```env
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_API_BASE=https://api.openai.com/v1
EMBEDDING_API_KEY=你的 embedding key
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

> 注意：Milvus collection 维度必须与 `EMBEDDING_DIMENSIONS` 一致；改维度会触发重建或需要手动清理旧 collection。

## 4. 执行 Checklist

### Phase 0：参考与计划

- [x] 读取 `nanobot` Provider Registry / Factory / Config 关键文件
- [x] 确认 `nanobot` 无完整 RAG embedding 模块
- [x] 创建改造计划文档：`docs/model_provider_plan.md`
- [x] 创建过程记录文档：`docs/model_provider_process.md`

### Phase 1：配置与 Provider Registry

- [x] 扩展 `app/config.py`：新增 LLM / Embedding provider 配置与兼容属性
- [x] 新增轻量 `app/core/model_provider_registry.py`
- [x] 改造 `app/core/llm_factory.py`：统一 OpenAI-compatible Chat 模型创建
- [x] 补 LLM factory 单元测试

### Phase 2：业务链路去 ChatQwen 强绑定

- [x] 改造 `app/services/rag_agent_service.py` 使用 `llm_factory`
- [x] 改造 AIOps planner/executor/replanner 使用 `llm_factory`
- [x] 保留测试注入能力，不访问真实模型

### Phase 3：Embedding 可禁用与 BM25-only 降级

- [x] 改造 `vector_embedding_service.py` 支持 `disabled` / OpenAI-compatible
- [x] 改造 `vector_store_manager.py`：embedding disabled 时跳过 dense vector store
- [x] 改造 `milvus_client.py`：向量维度读取配置
- [x] 改造 `main.py` / `health.py`：无 embedding 时不强制 Milvus
- [x] 改造 `vector_index_service.py`：docstore/BM25 成功后 dense 失败只记 warning
- [x] 补 embedding disabled 索引测试

### Phase 4：文档与示例配置

- [x] 更新 `.env.example`
- [x] 更新 `README.md` 模型与 embedding 配置说明
- [x] 过程文档记录每个阶段结果

### Phase 5：回归验证

- [x] 运行 Python 单元测试
- [x] 运行 Ruff 检查核心改动文件
- [x] 给出本地 `.env` 最小可运行方案

## 5. 完成标准

最终必须能回答：

```text
1. 你的项目怎么接任意 GPT 中转 / OpenAI-compatible 模型？
2. 为什么不照搬 nanobot 全量 provider registry？
3. 没 embedding key 时，RAG 怎么继续跑？
4. BM25-only 和 dense/hybrid 的切换点在哪里？
5. 如果以后拿到 embedding key，怎么恢复 dense + hybrid？
```
