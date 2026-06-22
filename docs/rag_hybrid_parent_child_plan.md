# RAG Hybrid Parent-Child 改造计划

> 目标：把当前 `dense vector top_k=3` 的 RAG 链路升级为可评估的 `Hybrid + Parent-Child` 检索架构。  
> 执行纪律：每完成一个步骤，必须在本文对应 checklist 打钩，并在 `docs/rag_hybrid_parent_child_process.md` 追加过程记录、验证命令和结果。

## 0. 当前基线

当前链路：

```text
文档上传
  -> Markdown h1/h2 分割
  -> RecursiveCharacterTextSplitter 二次切块，实际 chunk_size≈1600，overlap=100
  -> Milvus dense vector store
  -> retrieve_knowledge top_k=3
  -> LLM 生成答案
```

当前短板：

- 只有 dense retrieval，服务名、错误码、告警名等关键词可能漏召回。
- chunk 偏大，召回粒度粗。
- 没有 parent-child，上下文完整性和精准召回无法兼得。
- 没有检索模式对比，无法证明 BM25 / hybrid / parent-child 是否提升。

## 1. 改造目标

目标链路：

```text
文档入库：
Markdown / txt
  -> parent chunks：按章节聚合，保存完整上下文
  -> child chunks：按 500/80 小块切分，用于精确召回
  -> child 写入 Milvus dense index
  -> child 写入本地 docstore / BM25 index
  -> parent 写入 parent store

查询：
query
  -> dense child recall top N
  -> BM25 child recall top N
  -> merge by child_id
  -> rerank / score fusion
  -> expand parent context
  -> final top K
  -> LLM with citations
```

## 2. 设计原则

1. **一刀一验**：每个模块落地后必须有测试或评估命令。
2. **保持兼容**：不破坏当前 `/api/upload`、`retrieve_knowledge`、Milvus 写入流程。
3. **可回滚**：新检索模式通过配置开关启用，保留 `dense` 模式。
4. **先规则后模型**：第一版 rerank 用 score fusion / lexical boost，不接真实 reranker 模型。
5. **用指标说话**：改造前后必须用 `evals/golden_cases.json` 对比。

## 3. 参数初版

```python
rag_retrieval_mode = "hybrid_parent"  # dense | hybrid | hybrid_parent
rag_child_chunk_size = 500
rag_child_chunk_overlap = 80
rag_parent_max_chars = 3500
rag_dense_fetch_k = 10
rag_bm25_fetch_k = 10
rag_final_top_k = 3
rag_dense_weight = 0.6
rag_bm25_weight = 0.4
rag_expand_parent = True
rag_parent_context_max_chars = 2500
```

## 4. 文件规划

### 新增文件

```text
app/models/rag.py
app/services/rag_document_store.py
app/services/parent_child_splitter_service.py
app/services/bm25_retrieval_service.py
app/services/rerank_service.py
app/services/hybrid_retrieval_service.py
app/api/rag.py
data/rag/.gitkeep
scripts/eval_retrieval.py          # 已存在，需扩展模式对比
docs/rag_design.md
tests/test_parent_child_splitter_service.py
tests/test_rag_document_store.py
tests/test_bm25_retrieval_service.py
tests/test_hybrid_retrieval_service.py
tests/test_eval_retrieval_metrics.py
```

### 修改文件

```text
app/config.py
app/main.py
app/services/vector_index_service.py
app/services/vector_store_manager.py
app/tools/knowledge_tool.py
Makefile
README.md
```

## 5. 执行 Checklist

### Phase 0：文档与基线

- [x] 创建改造计划文档：`docs/rag_hybrid_parent_child_plan.md`
- [x] 创建过程记录文档：`docs/rag_hybrid_parent_child_process.md`
- [x] 记录当前 dense baseline 评估结果
- [x] 梳理当前 chunk 输出样例，确认真实 chunk size / metadata

### Phase 1：Parent-Child Splitter + Docstore

- [x] 新增 `app/models/rag.py`
- [x] 新增 `ParentDocument` / `ChildDocument` / `RetrievalCandidate` 模型
- [x] 新增 `parent_child_splitter_service.py`
- [x] 新增 `rag_document_store.py`
- [x] 新增 `data/rag/` 本地存储目录
- [x] 改造 `vector_index_service.py`：写 Milvus child + docstore parent/child
- [x] 补 splitter/docstore 单元测试
- [x] 验证索引文档后可生成 parent/child jsonl（service 级穿透，API 上传需服务环境复验）

验收命令：

```bash
.venv/bin/python -m pytest tests/test_parent_child_splitter_service.py tests/test_rag_document_store.py -v
```

### Phase 2：BM25 检索

- [x] 新增 `bm25_retrieval_service.py`
- [x] 支持从 `children.jsonl` 构建 BM25 索引
- [x] 支持中文字符/英文 token 混合 tokenize
- [x] 支持 `search(query, k)` 返回 `RetrievalCandidate`
- [x] 补 BM25 单元测试
- [x] 验证 CPU / 磁盘 / 内存关键词能命中对应 runbook

验收命令：

```bash
.venv/bin/python -m pytest tests/test_bm25_retrieval_service.py -v
```

### Phase 3：Hybrid Retrieval + Rerank

- [x] 新增 `rerank_service.py`
- [x] 新增 `hybrid_retrieval_service.py`
- [x] Dense recall 接入 Milvus child search
- [x] BM25 recall 接入 docstore child search
- [x] 支持按 `child_id` merge 去重
- [x] 支持 dense/bm25 score normalize 与融合
- [x] 支持 exact keyword / title boost
- [x] 支持按 `parent_id` expand parent context
- [x] 改造 `knowledge_tool.py` 使用 hybrid retrieval
- [x] 保留配置开关回退 dense 模式
- [x] 补 hybrid 单元测试

验收命令：

```bash
.venv/bin/python -m pytest tests/test_hybrid_retrieval_service.py -v
```

### Phase 4：评估脚本扩展

- [x] 扩展 `scripts/eval_retrieval.py` 支持 `--mode dense|hybrid|hybrid_parent`
- [x] 输出 hit_rate / recall@k / precision@k / MRR / latency
- [x] 生成 `evals/reports/retrieval_<mode>.json`
- [x] 新增 `make eval-rag MODE=...`
- [x] 对比 local baseline vs hybrid_parent 指标

验收命令：

```bash
make eval-rag MODE=dense
make eval-rag MODE=hybrid_parent
```

### Phase 5：Debug API + 文档

- [x] 新增 `POST /api/rag/retrieve_debug`
- [x] 返回 dense_score / bm25_score / rerank_score / channels / parent_id
- [x] 更新 `README.md` RAG 架构说明
- [x] 新增 `docs/rag_design.md`
- [x] 更新过程文档 Phase 5 总结

验收命令：

```bash
curl -sS -X POST http://127.0.0.1:9900/api/rag/retrieve_debug \
  -H 'Content-Type: application/json' \
  -d '{"query":"CPU 使用率高怎么排查","mode":"hybrid_parent"}'
```

### Phase 6：全量回归

- [x] `make test` 通过
- [x] `make eval-rag MODE=dense` 通过
- [x] `make eval-rag MODE=hybrid_parent` 通过
- [x] 手动上传样例文档并检索验证
- [x] 文档 checklist 全部更新

## 6. 完成标准

最终必须能回答：

```text
1. 当前 chunk 策略是什么？为什么 child=500/80？
2. BM25 解决了哪些 dense 不擅长的问题？
3. Parent-child 如何兼顾精准召回与上下文完整性？
4. dense / hybrid / hybrid_parent 指标对比如何？
5. 如何回退到旧 dense 模式？
```

## 7. 过程记录规则

每完成一个 checklist 项，必须在 `docs/rag_hybrid_parent_child_process.md` 追加：

```markdown
## YYYY-MM-DD HH:mm - 步骤名

- 状态：完成 / 部分完成 / 阻塞
- 改动：涉及文件
- 关键决策：为什么这么做
- 验证：命令 + 关键输出
- 风险：剩余问题
- 下一步：下一项 checklist
```
