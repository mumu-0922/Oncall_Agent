# RAG Hybrid Parent-Child 改造过程记录

> 配套计划：`docs/rag_hybrid_parent_child_plan.md`  
> 纪律：每完成一步，先更新计划文档 checklist，再在本文追加过程记录。禁止只改代码不记过程。

## 当前状态

```text
阶段：Phase 0 - 文档与基线
状态：进行中
下一步：记录当前 dense baseline 评估结果，并抽样查看当前 chunk 输出
```

## 过程记录

### 2026-06-22 - 创建改造计划与过程台账

- 状态：完成
- 改动：
  - 新增 `docs/rag_hybrid_parent_child_plan.md`
  - 新增 `docs/rag_hybrid_parent_child_process.md`
- 关键决策：
  - 将改造拆成 Phase 0~6，避免一次性大改不可验证。
  - 明确每一步必须同步 checklist 与过程记录。
  - 第一版 rerank 只做规则融合，不接真实模型，降低依赖与成本。
- 验证：
  - 已写入计划文档与过程文档。
- 风险：
  - 后续实现阶段必须避免 checklist 与实际代码状态漂移。
- 下一步：
  - 记录当前 dense baseline 评估结果。
  - 抽样检查当前 splitter 产生的 chunk 与 metadata。

## 记录模板

```markdown
### YYYY-MM-DD HH:mm - 步骤名

- 状态：完成 / 部分完成 / 阻塞
- 改动：
  - `path/to/file`
- 关键决策：
  - 决策与理由
- 验证：
  ```bash
  command
  ```
  关键输出：...
- 风险：
  - 剩余风险
- 下一步：
  - 下一项 checklist
```

### 2026-06-22 14:43 - Phase 0 dense baseline 与当前 chunk 样例

- 状态：完成
- 改动：
  - 更新 `docs/rag_hybrid_parent_child_plan.md` Phase 0 checklist
  - 更新 `docs/rag_hybrid_parent_child_process.md`
- 关键决策：
  - 先用现有 `make eval-retrieval` 固化 baseline，再进入结构改造，避免后续无法说明改动收益。
  - 当前 splitter 实际二次切块为 `chunk_size=1600`、`overlap=100`，不是配置名义上的 800。
- 验证：
  ```bash
  make eval-retrieval
  .venv/bin/python - <<'PY'
  from pathlib import Path
  from app.services.document_splitter_service import document_splitter_service
  p=Path('aiops-docs/cpu_high_usage.md').resolve()
  docs=document_splitter_service.split_document(p.read_text(encoding='utf-8'), p.as_posix())
  print(len(docs))
  for d in docs[:3]: print(len(d.page_content), d.metadata)
  PY
  ```
  关键输出：
  ```text
  provider=local k=3 cases=8
  hit_rate=0.875 recall@k=0.875 precision@k=0.2917 mrr=0.6458
  cpu_high_usage.md -> 3 个分片
  chunk lens: 172 / 640 / 1091
  metadata: h1/h2/_source/_extension/_file_name
  ```
- 风险：
  - 当前 `eval-retrieval` 的 local baseline 是词法近似，不代表真实 Milvus dense 表现。
  - `cpu_with_history_ticket` 未命中 `cpu_high_usage.md`，说明关键词和语义混合召回确有必要。
- 下一步：
  - Phase 1：实现 `ParentDocument` / `ChildDocument` / `RetrievalCandidate` 与 parent-child splitter、docstore。

### 2026-06-22 14:45 - Phase 1 Parent-Child Splitter 与 Docstore 主体实现

- 状态：部分完成
- 改动：
  - 新增 `app/models/rag.py`
  - 新增 `app/services/parent_child_splitter_service.py`
  - 新增 `app/services/rag_document_store.py`
  - 新增 `data/rag/.gitkeep`
  - 修改 `app/config.py`
  - 修改 `app/services/vector_store_manager.py`
  - 修改 `app/services/vector_index_service.py`
  - 新增 `tests/test_parent_child_splitter_service.py`
  - 新增 `tests/test_rag_document_store.py`
- 关键决策：
  - Milvus 继续只存 child chunk，parent/child 全量写本地 JSONL docstore，用于 BM25 和 parent expansion。
  - `VectorStoreManager.add_documents()` 增加可选 `ids`，parent-child 模式传 `child_id`，保证 Milvus id 与 docstore 可关联。
  - `rag_retrieval_mode` 默认仍为 `dense`，避免未完成 hybrid 前破坏现有问答。
- 验证：
  ```bash
  .venv/bin/python -m pytest tests/test_parent_child_splitter_service.py tests/test_rag_document_store.py -v
  ```
  关键输出：
  ```text
  3 passed
  ```
- 风险：
  - 尚未通过真实 `/api/upload` + Milvus 流程验证 `parents.jsonl` / `children.jsonl` 生成。
  - 当前 vector index 写入 child 后，旧 dense retriever 仍可工作，但历史旧索引需要重新上传文档才能带 parent_id/child_id metadata。
- 下一步：
  - Phase 2：实现 BM25 检索服务与关键词命中测试。

### 2026-06-22 14:47 - Phase 2 BM25 检索服务实现

- 状态：完成
- 改动：
  - 新增 `app/services/bm25_retrieval_service.py`
  - 新增 `tests/test_bm25_retrieval_service.py`
  - 更新 `docs/rag_hybrid_parent_child_plan.md` Phase 2 checklist
- 关键决策：
  - 第一版不用外部 `rank_bm25` 依赖，内置轻量 BM25，降低部署和锁文件扰动。
  - tokenizer 同时保留英文/数字 token、中文单字和中文 bigram，兼顾服务名、错误码和中文关键词。
  - `BM25RetrievalService.rebuild_index(children)` 支持测试和外部注入；默认运行时从 docstore `children.jsonl` lazy load。
- 验证：
  ```bash
  .venv/bin/python -m pytest tests/test_bm25_retrieval_service.py -v
  ```
  关键输出：
  ```text
  2 passed
  CPU / 磁盘 / OOM 查询分别命中 cpu_high_usage.md / disk_high_usage.md / memory_high_usage.md
  ```
- 风险：
  - 中文分词是轻量字符/bigram，不如 jieba/ES analyzer；后续若文档规模增大可替换 analyzer。
- 下一步：
  - Phase 3：实现 score fusion rerank、hybrid retrieval、parent expansion，并改造 `retrieve_knowledge`。

### 2026-06-22 14:49 - Phase 3 Hybrid Retrieval / Rerank / Knowledge Tool 接入

- 状态：完成
- 改动：
  - 新增 `app/services/rerank_service.py`
  - 新增 `app/services/hybrid_retrieval_service.py`
  - 修改 `app/tools/knowledge_tool.py`
  - 新增 `tests/test_hybrid_retrieval_service.py`
  - 更新 `docs/rag_hybrid_parent_child_plan.md` Phase 3 checklist
- 关键决策：
  - `rag_retrieval_mode=dense` 时保留原向量检索；`hybrid` / `hybrid_parent` 时进入新链路。
  - Dense 分数按 L2 距离反向归一化，BM25 正向归一化，再叠加 exact/title/channel boost。
  - `hybrid_parent` 模式会用 `parent_id` 拉父块内容并限制 `rag_parent_context_max_chars`，避免 token 爆炸。
- 验证：
  ```bash
  .venv/bin/python -m pytest tests/test_hybrid_retrieval_service.py -v
  ```
  关键输出：
  ```text
  3 passed
  merge 去重、score fusion、parent expansion 均通过单测
  ```
- 风险：
  - 真实 dense recall 依赖 Milvus 当前索引是否已重新写入 child metadata；旧索引可能无 parent_id。
  - 第一版 rerank 是规则融合，不是模型 reranker。
- 下一步：
  - Phase 4：扩展评估脚本支持 `dense|hybrid|hybrid_parent` 模式对比。


### 2026-06-22 15:06 - Phase 4 评估脚本扩展与 hybrid_parent 指标固化

- 状态：完成
- 改动：
  - 修改 `scripts/eval_retrieval.py`
  - 修改 `Makefile`
  - 新增 `tests/test_eval_retrieval_metrics.py`
  - 生成 `evals/reports/retrieval_hybrid_parent.json`
  - 更新 `docs/rag_hybrid_parent_child_plan.md` Phase 4 checklist
- 关键决策：
  - `--mode local|dense|hybrid|hybrid_parent` 统一评估入口；旧 `--provider app` 映射到 `dense` 保持兼容。
  - `hybrid_parent` 离线评估先走 parent-child + BM25 + rule rerank，不依赖 Milvus，保证面试演示时可复现。
  - 报告输出命名为 `evals/reports/retrieval_<mode>.json`，便于后续横向对比。
- 验证：
  ```bash
  .venv/bin/python -m pytest tests/test_eval_retrieval_metrics.py -v
  make eval-rag MODE=hybrid_parent
  ```
  关键输出：
  ```text
  tests/test_eval_retrieval_metrics.py: 2 passed
  mode=hybrid_parent k=3 cases=8
  hit_rate=0.875 recall@k=0.875 precision@k=0.5833 mrr=0.875 latency_ms≈196.94
  wrote: evals/reports/retrieval_hybrid_parent.json
  ```
- 指标对比：
  ```text
  local baseline:   hit_rate=0.875 recall@k=0.875 precision@k=0.2917 mrr=0.6458
  hybrid_parent:   hit_rate=0.875 recall@k=0.875 precision@k=0.5833 mrr=0.875
  ```
- 风险：
  - 当前 `hybrid_parent` 评估是离线 BM25/parent-child 路径，不等同于线上 Milvus dense + BM25 全链路。
  - `cpu_with_history_ticket` 仍未命中 `cpu_high_usage.md`，后续可通过 query rewrite / synonym / 事件字段抽取继续补召回。
- 下一步：
  - Phase 5：新增 `/api/rag/retrieve_debug`，把 dense_score / bm25_score / rerank_score / channels / parent_id 暴露出来。


### 2026-06-22 15:13 - Phase 1 遗留验证：索引后生成 parent/child JSONL

- 状态：完成（service 级穿透）
- 改动：
  - 新增 `tests/test_vector_index_parent_child_docstore.py`
  - 更新 `docs/rag_hybrid_parent_child_plan.md` Phase 1 遗留 checklist
- 关键决策：
  - 不依赖真实 Milvus / DashScope，使用 fake `vector_store_manager` 穿透 `vector_index_service.index_single_file()`。
  - 验证重点放在 `/api/upload` 共用的索引服务：删除旧 source、parent-child split、写 `parents.jsonl` / `children.jsonl` / `index_manifest.json`、child ids 传给向量层。
- 验证：
  ```bash
  .venv/bin/python -m pytest tests/test_vector_index_parent_child_docstore.py -v
  ```
  关键输出：
  ```text
  1 passed
  cpu_high_usage.md -> parents=8, children=10
  ```
- 风险：
  - 这是 service 级穿透验证，不是启动 FastAPI 后的真实 multipart 上传；真实上传仍需 Milvus/API 服务可用时执行 `make test-upload` 或 curl。
- 下一步：
  - Phase 5：完成 Debug API 与 RAG 设计文档。


### 2026-06-22 15:16 - Phase 5 Debug API 与 RAG 设计文档

- 状态：完成
- 改动：
  - 新增 `app/api/rag.py`
  - 修改 `app/main.py` 注册 RAG 调试路由
  - 修改 `app/models/rag.py` 增加 Debug API request/response model
  - 修改 `app/services/hybrid_retrieval_service.py`，dense 不可用时降级为空 recall，避免 hybrid debug 因 Milvus 未启动直接失败
  - 新增 `tests/test_rag_debug_api.py`
  - 新增 `docs/rag_design.md`
  - 更新 `README.md`
  - 更新 `docs/rag_hybrid_parent_child_plan.md` Phase 5 checklist
- 关键决策：
  - Debug API 只做 read-only retrieval，不调用 LLM，便于定位召回、融合、parent expansion 问题。
  - 返回 `child_id` / `parent_id` / `dense_score` / `bm25_score` / `rerank_score` / `retrieval_channels`，把 RAG 黑盒拆成可解释证据。
  - `hybrid_parent` 在 Milvus 不可用时仍可用 BM25/docstore 返回候选，提升面试演示稳定性。
- 验证：
  ```bash
  .venv/bin/python -m py_compile app/models/rag.py app/services/hybrid_retrieval_service.py app/api/rag.py app/main.py
  .venv/bin/python -m pytest tests/test_rag_debug_api.py tests/test_hybrid_retrieval_service.py -v
  ```
  关键输出：
  ```text
  4 passed
  ```
- 风险：
  - `dense` 模式仍依赖 Milvus；服务环境未启动时 Debug API 的 `dense` 结果可能为空或报错。
  - 当前 Debug API 暴露 metadata，若未来 metadata 中出现敏感字段，需要做脱敏白名单。
- 下一步：
  - Phase 6：运行全量测试和 `eval-rag`，修复回归问题。


### 2026-06-22 15:20 - Phase 6 全量回归、真实上传验证与隐患修复

- 状态：完成
- 改动：
  - 修改 `app/services/parent_child_splitter_service.py`：缩短 `parent_id` / `child_id`，适配 Milvus varchar 主键长度限制
  - 修改 `scripts/eval_retrieval.py`：`hybrid_parent` 离线评估固定从 `--docs-dir` 构建 in-memory BM25，不再读取运行时 `data/rag`
  - 修改 `.gitignore`：忽略 `data/rag/*.jsonl` 与 `data/rag/index_manifest.json` 运行时 docstore
  - 更新 `tests/test_parent_child_splitter_service.py`：新增 id 长度回归测试
  - 更新 `docs/rag_hybrid_parent_child_plan.md` Phase 6 checklist
- 关键决策：
  - 真实上传暴露出 `child_id` 最长 109 字符，超过 Milvus 主键 `varchar max_length=100`，因此将 id 改为短 hash 结构：`p::<hash>::<index>::<hash>` / `c::<hash>::<index>::<hash>`。
  - 离线评估必须可复现，不应被真实上传后的 `data/rag` 局部 docstore 污染，所以评估脚本改为每次从 `aiops-docs/` 构建 benchmark 索引。
  - `data/rag` 是运行时状态，只保留 `.gitkeep` 入库，避免提交个人/临时上传内容。
- 验证：
  ```bash
  make test
  make eval-rag MODE=local
  make eval-rag MODE=hybrid_parent
  make eval-rag MODE=dense   # 外层网络执行，连接本机 Milvus
  curl -sS -f http://127.0.0.1:9900/health
  curl -sS -f -X POST http://127.0.0.1:9900/api/upload -F 'file=@aiops-docs/cpu_high_usage.md'
  curl -sS -f -X POST http://127.0.0.1:9900/api/rag/retrieve_debug \
    -H 'Content-Type: application/json' \
    -d '{"query":"CPU 使用率高怎么排查","mode":"hybrid_parent","top_k":3}'
  ```
  关键输出：
  ```text
  make test: 16 passed
  local: hit_rate=0.875 recall@k=0.875 precision@k=0.2917 mrr=0.6458
  hybrid_parent: hit_rate=0.875 recall@k=0.875 precision@k=0.5833 mrr=0.875
  dense: hit_rate=1.0 recall@k=1.0 precision@k=0.5833 mrr=0.875
  /api/upload: code=200, 向量索引创建成功，parents=8, children=10
  /api/rag/retrieve_debug: candidate_count=3，top3 均为 cpu_high_usage.md，channels=[dense,bm25]，expanded_parent=True
  child_id 长度=32，parent_id 长度=36，均小于 Milvus max_length=100
  ```
- 风险：
  - 当前 Milvus collection 里仍可能存在历史旧 chunk；生产切换前建议重建 collection 或全量重传，避免新旧 metadata 混杂。
  - `hybrid_parent` 线上效果依赖 `data/rag` 是否包含完整知识库；只上传单个文件时 BM25 只能覆盖单文件。
  - `dense` 评估需要 Milvus、DashScope embedding 和外层网络可用；受限沙箱内会连不上 `localhost:19530`。
- 下一步：
  - 面试展示时先跑 `make eval-rag MODE=hybrid_parent`，再用 `/api/rag/retrieve_debug` 展示分数与 channel，最后说明 Milvus id 长度坑和评估污染坑是如何发现并修复的。


### 2026-06-22 15:24 - 安全与提交边界抽检

- 状态：完成
- 改动：
  - 更新 `.gitignore`，忽略 RAG 运行时 docstore 文件
  - 抽检 `app/` / `docs/` / `scripts/` / `tests/` / `README.md` 中的敏感信息模式
- 关键决策：
  - `data/rag/*.jsonl` 与 `data/rag/index_manifest.json` 属于运行时状态，不应随代码提交；保留 `data/rag/.gitkeep` 作为目录占位。
  - Debug API 当前会返回 metadata，设计文档和过程文档已记录未来需做 metadata 脱敏白名单的风险。
  - `.env` 是本地环境文件且包含真实 API Key，应保持本地使用，不进入提交。
- 验证：
  ```bash
  grep -RInE "(sk-[A-Za-z0-9]|api[_-]?key\s*=|password\s*=|secret\s*=|token\s*=)" app docs README.md scripts tests Makefile .gitignore
  git status --short
  ```
  关键输出：
  ```text
  未发现硬编码 sk-* 密钥进入 app/docs/scripts/tests/README；仅发现 config.dashscope_api_key 这类配置引用。
  .env 仍为本地 modified，含真实 key，禁止提交。
  ```
- 风险：
  - 如果要提交本次改造，需要显式排除 `.env`、运行时日志、PID、上传文件和 `data/rag/*.jsonl`。
- 下一步：
  - 面试演示前可运行 `git status --short` 确认提交边界，再运行 `make eval-rag MODE=hybrid_parent` 与 Debug API。
