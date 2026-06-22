# 前端开发分离、生产一体迁移计划

- **目标**：把项目调整为开发态 Vite/FastAPI 分离、生产态 FastAPI 托管前端构建产物。
- **日期**：2026-06-22
- **状态**：执行中

## 劫关

- [x] 1. 侦察当前 `static/`、`app/main.py`、`Makefile`、`README.md` 的真实链路。
- [x] 2. 新增 `frontend/`，迁移现有静态页面为 Vite 源码。
- [x] 3. 统一前端 API base，默认走同源 `/api`，开发态由 Vite proxy 转发。
- [x] 4. 增强 FastAPI 静态托管，支持生产首页与 SPA fallback。
- [x] 5. 补齐 Makefile：`web-dev`、`web-build`、`api-dev`、`dev-all`。
- [x] 6. 更新 README/ADR/过程文档，修正健康检查路径。
- [x] 7. 安装前端依赖，执行前端 build，刷新 `static/` 生产产物。
- [x] 8. 运行后端测试与最小前端构建验证。

## 验收标准

- 开发态：`make api-dev` + `make web-dev` 可分离运行，浏览器访问 `http://localhost:5173`。
- 生产态：`static/index.html` 来自前端构建产物，FastAPI `/` 单端口访问可用。
- API 请求：前端默认请求 `/api/*`，不再写死 `http://localhost:9900/api`。
- 回归：`make test` 通过，`npm --prefix frontend run build` 通过。
