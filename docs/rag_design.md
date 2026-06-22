# RAG Hybrid Parent-Child 设计说明

> 面试展示口径：这个项目不再只是“会调用 LangChain / Milvus”，而是补齐了可解释、可回退、可评估的 RAG 工程链路。

## 1. 当前检索架构

### 默认兼容链路：`dense`

```text
upload / index_directory
  -> parent-child splitter 生成 child chunks
  -> child chunks 写入 Milvus
  -> retrieve_knowledge 使用 Milvus dense top_k
```

默认 `RAG_RETRIEVAL_MODE=dense`，保证旧问答链路不被新实验破坏。

### 新增链路：`hybrid_parent`

```text
文档入库：
Markdown / txt
  -> parent chunks：按 Markdown # / ## 章节聚合，最长约 3500 chars
  -> child chunks：每个 parent 内切 500 chars，overlap 80
  -> child 写 Milvus dense index
  -> parent / child 写本地 JSONL docstore
  -> BM25 从 children.jsonl 构建轻量 sparse index

查询：
query
  -> dense recall child top N
  -> BM25 recall child top N
  -> merge by child_id
  -> dense/BM25 score normalize + rule rerank
  -> top K child 按 parent_id 扩展 parent window
  -> LLM 生成答案 / Debug API 返回证据
```

## 2. Chunk 策略

旧 splitter 实际行为：

```text
Markdown 先按 # / ## 切分
RecursiveCharacterTextSplitter 二次切块
实际 chunk_size = config.chunk_max_size * 2 = 1600
overlap = 100
```

新 parent-child 策略：

| 层级 | 参数 | 作用 |
|------|------|------|
| parent | `RAG_PARENT_MAX_CHARS=3500` | 保留 runbook 章节级上下文，供最终注入 |
| child | `RAG_CHILD_CHUNK_SIZE=500` | 提升召回粒度，避免大块稀释关键词 |
| child overlap | `RAG_CHILD_CHUNK_OVERLAP=80` | 保留跨句/跨步骤连续性，约 16% overlap |
| parent context | `RAG_PARENT_CONTEXT_MAX_CHARS=2500` | 命中 child 后扩展局部 parent window，控制 token 成本 |

为什么要父子切块：

- child 小：适合 dense/BM25 精准召回，比如 `HighCPUUsage`、`OOM`、`DiskFull`。
- parent 大：适合生成答案时保留完整排查步骤和上下文。
- 单纯大 chunk 召回粗，单纯小 chunk 容易上下文断裂；parent-child 同时解决两端问题。

## 3. BM25 如何使用

实现文件：`app/services/bm25_retrieval_service.py`

BM25 索引来源：

```text
data/rag/children.jsonl
```

每个 child 的索引文本：

```text
file_name + title_path + child.content
```

Tokenizer 策略：

- 英文/数字/下划线 token：保留服务名、告警名、错误码。
- 中文单字：保证基础召回。
- 中文 bigram：提升“磁盘”“内存”“使用”等短语匹配。

BM25 解决 dense 的短板：

- 告警名、错误码、服务名等 lexical exact match。
- 用户问题里出现明确关键词时，避免 embedding 语义漂移。
- 可解释：Debug API 能直接看到 `bm25_score` 与 `retrieval_channels`。

## 4. Rerank / Score Fusion

实现文件：`app/services/rerank_service.py`

第一版不用模型 reranker，采用规则融合：

```text
final_score = dense_weight * dense_norm
            + bm25_weight * bm25_norm
            + exact/title boost
            + dual-channel boost
```

默认参数：

```text
RAG_DENSE_WEIGHT=0.6
RAG_BM25_WEIGHT=0.4
RAG_DENSE_FETCH_K=10
RAG_BM25_FETCH_K=10
RAG_FINAL_TOP_K=3
```

这样做的面试价值：

- 能说清楚 reranker 的输入输出：输入 query + 候选 child，输出排序后的候选列表。
- 能解释为什么先规则后模型：降低依赖、成本和不可控性，先建立评估闭环。
- 后续可替换为 cross-encoder / bge-reranker，接口不变。

## 5. Debug API

接口：`POST /api/rag/retrieve_debug`

请求：

```json
{
  "query": "CPU 使用率高怎么排查",
  "mode": "hybrid_parent",
  "top_k": 3
}
```

返回字段：

```text
child_id / parent_id
source / file_name / title_path
dense_score / bm25_score / rerank_score
retrieval_channels
content_preview
metadata
```

用途：

- 面试时展示“为什么召回这几篇”。
- 定位 dense/BM25/rerank 哪一段出了问题。
- 对比 `dense`、`hybrid`、`hybrid_parent` 的候选差异。

## 6. 评估闭环

评估数据：`evals/golden_cases.json`

命令：

```bash
make eval-retrieval
make eval-rag MODE=local
make eval-rag MODE=hybrid_parent
```

当前记录：

| 模式 | hit_rate | recall@k | precision@k | MRR |
|------|----------|----------|-------------|-----|
| local baseline | 0.875 | 0.875 | 0.2917 | 0.6458 |
| hybrid_parent | 0.875 | 0.875 | 0.5833 | 0.875 |

解释：

- hit_rate / recall@k 未变，说明 golden cases 仍有一个难例未召回。
- precision@k 与 MRR 提升，说明相关文档排序更靠前、无关候选更少。
- 剩余 miss：`cpu_with_history_ticket`，后续应加 query rewrite、synonym、告警字段抽取。

## 7. 回退策略

`.env` 中设置：

```bash
RAG_RETRIEVAL_MODE=dense
```

即可回到旧 dense 检索路径。

新增文件均围绕 parent-child / BM25 / hybrid service，不删除旧 `document_splitter_service.py` 和旧 dense retriever，因此回滚面小。

## 8. 已知边界

- `hybrid_parent` 离线评估当前主要证明 BM25 + parent-child + rerank；真实 dense + BM25 线上效果需要 Milvus 重新上传文档后验证。
- 旧 Milvus collection 中历史文档如果没有 `child_id/parent_id` metadata，无法完整 parent expansion，需重新上传。
- BM25 tokenizer 是轻量实现，不等同生产级中文 analyzer；文档规模增大后可替换为 ES/OpenSearch 或 jieba/analyzer。
- 当前 rerank 是规则融合，不是训练/模型 reranker；优势是可解释、低成本，短板是复杂语义排序能力有限。
