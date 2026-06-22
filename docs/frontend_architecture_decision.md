# 前端架构决策：开发态分离，生产态一体托管

- **状态**：Accepted
- **日期**：2026-06-22
- **范围**：前端开发、构建产物、FastAPI 静态托管、演示启动入口

## 判定

项目采用双态前端架构：

1. **开发态**：前端 Vite 与 FastAPI 分离运行。
2. **生产态**：`npm run build` 输出到仓库根目录 `static/`，由 FastAPI 一体托管。
3. **演示态**：`make one` 保持单端口演示入口，访问 `http://localhost:9900`。

## 背景

FastAPI 负责 API、Agent 编排、RAG、AIOps 与 MCP 调用。前端在开发时需要 Vite 的 HMR、模块化构建与独立依赖管理；但部署和演示时，项目需要保持一个入口，降低启动和验收成本。

因此不把 Vite dev server 带入生产态，也不让 FastAPI 在开发态承担前端构建职责。

## 决策

### 1. 开发态：Vite 与 FastAPI 分离

- FastAPI 独立启动在 `9900`，只负责 API 与后端能力。
- Vite 独立启动在 `5173`（或 Vite 默认端口），负责前端页面、HMR 与前端源码调试。
- 浏览器开发入口使用 Vite 地址，例如：`http://localhost:5173`。
- 前端请求后端 API 时统一走相对路径 `/api/...`，由 Vite dev proxy 转发到 `http://127.0.0.1:9900`。

建议开发命令：

```bash
# 终端 1：后端 API
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 9900

# 终端 2：前端 Vite
cd frontend
npm ci
npm run dev
```

### 2. 生产态：构建产物进入 static，由 FastAPI 托管

- 前端源码位于 `frontend/`。
- `npm run build` 生成生产静态资源。
- 构建产物输出或同步到仓库根目录 `static/`。
- FastAPI 挂载 `static/`，提供 `/`、`/static/*` 与 `/api/*`。
- 生产访问入口使用 FastAPI 地址，例如：`http://localhost:9900`。

建议生产构建命令：

```bash
cd frontend
npm ci
npm run build
cd ..
```

构建完成后，`static/` 是 FastAPI 的前端托管目录。前端运行时不依赖 Vite dev server。

### 3. make one：单端口演示不变

`make one` 保持演示闭环：启动 Milvus、MCP、FastAPI，上传示例知识库，并打印统一访问入口。

- 前端页面：`http://localhost:9900`
- API 文档：`http://localhost:9900/docs`
- 健康检查：`http://localhost:9900/health`

`make one` 不应启动独立 Vite dev server。若前端源码有变更，应先执行 `npm run build` 将产物刷新到 `static/`，再运行 `make one`。

## 目录约定

```text
frontend/                 # Vite 前端源码；开发态独立运行
  package.json            # npm scripts 与前端依赖
  vite.config.*           # Vite 配置，开发态代理 /api 到 FastAPI
  src/                    # 前端业务源码

static/                   # 生产构建产物；FastAPI 一体托管
  index.html              # 生产首页
  assets/                 # Vite 构建资源，或兼容既有静态文件

app/main.py               # FastAPI 入口，挂载 static/ 并注册 API routes
Makefile                  # make one 保持单端口演示编排
```

## 约束

- 不在开发态手工编辑 `static/` 来替代 Vite 源码开发。
- 不在生产态依赖 Vite dev server。
- 前端 API base 优先保持相对路径 `/api`，避免 dev/prod 地址漂移。
- 不提交 `node_modules/`、本地日志、PID 文件或敏感配置。
- `make one` 的职责是演示编排，不承担前端源码开发服务。

## 权衡

- **收益**：开发体验保留 Vite HMR；生产和演示保持 FastAPI 单入口，部署简单。
- **成本**：前端源码变更后需要显式构建并刷新 `static/`。
- **风险**：若 Vite proxy、API base 或 build 输出目录漂移，会造成开发态可用但生产态失败。

## 验收标准

- 开发态可同时启动 FastAPI `9900` 与 Vite `5173`，前端通过 `/api` 调用后端。
- 生产构建后，`static/index.html` 可由 FastAPI `/` 返回。
- `make one` 只暴露 `http://localhost:9900` 作为前端演示入口。
