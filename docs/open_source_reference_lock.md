# Open-source Reference Lock

> 生成时间：2026-06-24；用途：记录本地隔离参考仓库的来源、commit 与取经边界。

| Project | Local path | Commit | Pull mode | Learn from | License file |
|---|---|---:|---|---|---|
| holmesgpt | `reference-projects/holmesgpt` | `b046938` | sparse: README/LICENSE/pyproject/holmes | observability toolset, RCA, read-only guardrails | LICENSE |
| k8sgpt | `reference-projects/k8sgpt` | `6ad7585` | shallow: full small repo | analyzer-first diagnostics, structured findings | LICENSE |
| keep | `reference-projects/keep` | `bdf2619` | root metadata only | alert dedup, correlation, workflow/enrichment | LICENSE |
| ragflow | `reference-projects/ragflow` | `398f488` | root metadata only | RAG pipeline, chunking, citations | LICENSE |
| langfuse | `reference-projects/langfuse` | `48cac44` | root metadata only | LLM tracing, prompt version, datasets/evals | LICENSE |
| promptfoo | `reference-projects/promptfoo` | `defd4bb` | root metadata only | prompt/agent/RAG regression and red-team evals | LICENSE |
| ragas | `reference-projects/ragas` | `298b682` | root metadata only | RAG faithfulness/context precision/recall metrics | LICENSE |

## 边界

- 此 lock 只证明本地参考源，不代表主工程依赖这些项目。
- `reference-projects/*` 被 `.gitignore` 忽略，第三方源码不提交。
- 真正魔改只能重写到主项目模块，并配套测试。
