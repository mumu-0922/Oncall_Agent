# 前端开发分离、生产一体迁移过程记录

## 2026-06-22

- [x] 侦察完成：确认旧前端位于 `static/`，`static/app.js` 写死 `http://localhost:9900/api`，FastAPI 只托管 `/` 与 `/static/*`，无 SPA fallback。
- [x] 多 Agent 协同完成：Scout 给出风险清单；文档 Worker 新增 ADR 并更新 README；后端 Worker 超时后由吾接管后端静态托管改造。
- [x] 新建 `frontend/`：从旧 `static/index.html`、`static/app.js`、`static/styles.css` 迁移为 Vite 源码。
- [x] 新增 `frontend/package.json`、`frontend/vite.config.js`、`frontend/.env.example`、`frontend/README.md`。
- [x] 前端 API base 统一：源码默认 `/api`；开发态通过 Vite proxy 转发到 `http://127.0.0.1:9900`；旧 `static/app.js` 也改为同源 `/api` 以兼容未构建场景。
- [x] FastAPI 增强：新增 SPA fallback，保留 `/api`、`/docs`、`/openapi.json`、`/health`、`/static` 等后端路径语义。
- [x] Makefile 命令补齐：新增 `api-dev`、`web-install`、`web-dev`、`web-build`、`web-preview`、`dev-all`，`make dev` 指向后端开发态。
- [x] README/ADR 最终校正：补充双态架构说明，健康检查统一为 `/health`。
- [x] 前端依赖安装与 build 验证：`npm --prefix frontend install` 成功，`npm --prefix frontend run build` 成功，产物输出到 `static/`；随后关闭 sourcemap 并重建，避免提交 `.map` 产物。
- [x] 后端测试验证：`pytest tests/test_static_hosting.py -q` 通过，`pytest tests/ -q` 全量 20 passed，`ruff check app/main.py tests/test_static_hosting.py` 通过。
