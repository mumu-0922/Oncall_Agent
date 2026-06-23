# Model Provider 与 Embedding 降级改造过程记录

> 配套计划：`docs/model_provider_plan.md`  
> 纪律：每完成一步，先更新计划 checklist，再在本文追加过程记录。

## 当前状态

```text
阶段：Phase 1 - 配置与 Provider Registry
状态：进行中
下一步：改造 config / provider registry / llm_factory
```

## 过程记录

### 2026-06-22 17:25 - Phase 0 nanobot 参考与计划落档

- 状态：完成
- 改动：
  - 新增 `docs/model_provider_plan.md`
  - 新增 `docs/model_provider_process.md`
- 关键决策：
  - 借鉴 `nanobot` 的 ProviderSpec + Factory 思路，但不照搬其完整 provider 层，避免把当前项目改成通用 Agent 框架。
  - 先解决当前痛点：GPT 中转可接入、ChatQwen 去绑定、embedding 可禁用、RAG 可 BM25-only 降级。
  - `nanobot` 无完整 RAG embedding 模块，因此 embedding 方案由本项目按 RAG 链路自建。
- 验证：
  ```bash
  sed -n '1,260p' /home/mumu/projects/nanobot/nanobot/providers/registry.py
  sed -n '1,260p' /home/mumu/projects/nanobot/nanobot/providers/factory.py
  grep -RniE "embedding|embeddings" /home/mumu/projects/nanobot/nanobot /home/mumu/projects/nanobot/docs /home/mumu/projects/nanobot/tests | head
  ```
  关键输出：
  ```text
  ProviderSpec registry + make_provider factory 存在；embedding 仅命中文档/测试文本，无 RAG embedding 服务。
  ```
- 风险：
  - “接所有模型”的前提是模型服务提供 OpenAI-compatible Chat Completions；非兼容厂商仍需单独 backend。
- 下一步：
  - Phase 1：配置与 LLM factory 改造。

### 2026-06-22 17:33 - Phase 1 Provider 配置与 LLM Factory

- 状态：完成
- 改动：
  - `app/config.py`
  - `app/core/model_provider_registry.py`
  - `app/core/llm_factory.py`
  - `tests/test_llm_factory.py`
- 关键决策：
  - 新增 `LLM_PROVIDER` / `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL`，让 GPT 中转和 OpenAI-compatible 网关不再借用 DashScope 配置名。
  - 保留 `DASHSCOPE_*` 兼容旧配置；`LLM_MODEL` 优先级高于 `RAG_MODEL`，避免切 GPT 中转时仍误用 qwen 模型名。
  - Provider registry 只做轻量元数据和 base_url 识别，不引入 nanobot 的 full backend/fallback 复杂度。
- 验证：
  ```bash
  .venv/bin/python -m pytest tests/test_llm_factory.py -q
  ```
  关键输出：
  ```text
  3 passed
  ```
- 风险：
  - 当前工厂覆盖 OpenAI-compatible Chat；Anthropic/Gemini native API 不在第一版范围，需通过兼容网关或后续加 backend。
- 下一步：
  - Phase 2：业务链路去 ChatQwen 强绑定。

### 2026-06-22 17:34 - Phase 2 RAG Agent 与 AIOps 去 ChatQwen 强绑定

- 状态：完成
- 改动：
  - `app/services/rag_agent_service.py`
  - `app/agent/aiops/planner.py`
  - `app/agent/aiops/executor.py`
  - `app/agent/aiops/replanner.py`
- 关键决策：
  - RAG Agent、Planner、Executor、Replanner 统一调用 `llm_factory.create_chat_model()`。
  - 单测仍可注入 fake model，不触发真实网络请求。
- 验证：
  ```bash
  grep -RniE "ChatQwen|langchain_qwq" app tests | grep -v __pycache__
  .venv/bin/python -m pytest tests/test_rag_agent_service.py -q
  ```
  关键输出：
  ```text
  app 业务链路无 ChatQwen 导入；rag_agent_service 单测通过。
  ```
- 风险：
  - 已安装依赖中仍有 `langchain-qwq`，但代码不再直接使用；后续可单独清理依赖和 lock。
- 下一步：
  - Phase 3：Embedding 可禁用与 BM25-only 降级。

### 2026-06-22 17:35 - Phase 3 Embedding disabled 与 BM25-only 降级

- 状态：完成
- 改动：
  - `app/services/vector_embedding_service.py`
  - `app/services/vector_store_manager.py`
  - `app/services/vector_index_service.py`
  - `app/core/milvus_client.py`
  - `app/main.py`
  - `app/api/health.py`
  - `tests/test_embedding_disabled_indexing.py`
- 关键决策：
  - `EMBEDDING_PROVIDER=disabled` 时不在 import 阶段报错，返回 `DisabledEmbeddings`。
  - 上传/索引先写 parent-child docstore，再 rebuild BM25；dense embedding/Milvus 失败只 warning，不阻断上传。
  - FastAPI 启动在 embedding disabled 时跳过 Milvus/VectorStore，健康检查返回 `milvus.status=skipped`。
  - Milvus 向量维度改读 `EMBEDDING_DIMENSIONS`，为 OpenAI embedding 1536 维等场景留出配置面。
- 验证：
  ```bash
  .venv/bin/python -m pytest tests/test_embedding_disabled_indexing.py tests/test_vector_index_parent_child_docstore.py -q
  EMBEDDING_PROVIDER=disabled LLM_PROVIDER=openai_compatible LLM_API_BASE=https://gateway.example/v1 LLM_API_KEY=sk-test-123456 LLM_MODEL=gpt-test .venv/bin/python - <<'PY'
  from app.services.rag_agent_service import RagAgentService
  s=RagAgentService(streaming=False)
  print(type(s.model).__name__, s.model_name)
  PY
  ```
  关键输出：
  ```text
  embedding disabled 索引测试通过；RagAgentService 构建 ChatOpenAI gpt-test，不访问真实模型。
  ```
- 风险：
  - BM25-only 需要 docstore 中已有文档；空库时检索不会凭空返回结果。
  - dense 恢复后要注意 Milvus collection 维度和历史数据重建。
- 下一步：
  - Phase 4：配置样例和 README 更新。

### 2026-06-22 17:38 - Phase 4 文档与示例配置

- 状态：完成
- 改动：
  - `.env.example`
  - `README.md`
  - `docs/model_provider_plan.md`
  - `docs/model_provider_process.md`
- 关键决策：
  - README 的默认推荐从 DashScope-only 改成 GPT 中转 / OpenAI-compatible 优先。
  - 明确三种配置：GPT 中转无 embedding、DashScope 全家桶、OpenAI embedding。
  - 常见问题里不再建议打印 API Key，只打印 provider/base/model 等非敏感配置。
- 验证：
  ```bash
  grep -n "LLM\|Embedding\|API Key" README.md | head
  ```
  关键输出：
  ```text
  README 已包含 LLM_PROVIDER / EMBEDDING_PROVIDER / BM25-only 降级说明。
  ```
- 风险：
  - Windows 旧脚本仍是传统多窗口启动说明，后续可再把 one/dev-all 体验同步过去。
- 下一步：
  - Phase 5：全量测试、lint、最小运行配置验证。

### 2026-06-22 17:39 - Phase 5 回归验证与最小运行配置

- 状态：完成
- 改动：
  - `docs/model_provider_plan.md`
  - `docs/model_provider_process.md`
- 关键决策：
  - 只对本次核心改动文件执行 Ruff，避免旧代码大面积历史 lint 噪声掩盖本次质量。
  - 用全量 `tests/` 回归确保 RAG、前端托管、provider factory、embedding disabled 均未破坏。
  - 用直接调用 `/health` handler 验证 embedding disabled 时返回 healthy 且 Milvus 为 skipped。
- 验证：
  ```bash
  .venv/bin/python -m ruff check app/config.py app/core/model_provider_registry.py app/core/llm_factory.py app/services/rag_agent_service.py app/agent/aiops/planner.py app/agent/aiops/executor.py app/agent/aiops/replanner.py app/services/vector_embedding_service.py app/services/vector_store_manager.py app/services/vector_index_service.py app/core/milvus_client.py app/main.py app/api/health.py tests/test_llm_factory.py tests/test_embedding_disabled_indexing.py
  .venv/bin/python -m pytest tests/ -q
  EMBEDDING_PROVIDER=disabled LLM_PROVIDER=openai_compatible LLM_API_BASE=https://gateway.example/v1 LLM_API_KEY=sk-test-123456 LLM_MODEL=gpt-test RAG_RETRIEVAL_MODE=hybrid_parent .venv/bin/python - <<'PY'
  import asyncio
  from app.api.health import health_check
  async def main():
      r = await health_check()
      print(r.status_code)
      print(r.body.decode())
  asyncio.run(main())
  PY
  ```
  关键输出：
  ```text
  Ruff: All checks passed
  Pytest: 24 passed
  /health: 200, embedding.enabled=false, milvus.status=skipped, llm.model=gpt-test
  ```
- 风险：
  - 测试未真实请求用户 GPT 中转，避免消耗 key；真实连通性仍需用户填入实际 `LLM_API_BASE/LLM_API_KEY/LLM_MODEL` 后用 `/api/chat` 验证。
  - 当前本地 `.env` 仍有旧 DashScope 配置；要切换需按下方最小配置更新。
- 下一步：
  - 若魔尊确认，要把 `.env` 改为实际 GPT 中转配置后，本地启动并测 `/api/chat`。

#### 最小可运行 `.env`（GPT 中转 + 无 embedding）

```env
LLM_PROVIDER=openai_compatible
LLM_API_BASE=https://你的中转地址/v1
LLM_API_KEY=你的中转key
LLM_MODEL=你的模型名

EMBEDDING_PROVIDER=disabled
RAG_RETRIEVAL_MODE=hybrid_parent
```
