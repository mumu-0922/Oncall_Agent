# SuperBizAgent

> 企业级智能对话和运维助手，支持 RAG 知识库问答和 AIOps 智能诊断

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-latest-orange.svg)](https://www.langchain.com/)

## ✨ 核心特性

- 🤖 **智能对话** - LangChain 多轮对话 + 流式输出
- 📚 **RAG 问答** - 支持 dense / hybrid / hybrid_parent 检索，文档上传后自动建立 Milvus child index、parent-child docstore 与 BM25 sparse index
- 🔧 **AIOps 诊断** - Plan-Execute-Replan 自动故障诊断和根因分析
- 🌐 **Web 界面** - 现代化 UI，支持多种对话模式：快速问答/流式对话
- 🔌 **MCP 集成** - 日志查询和监控数据工具接入

## 🛠️ 技术栈

- **框架**: FastAPI + LangChain + LangGraph
- **前端**: Vite 开发态分离，`npm run build` 输出 `static/` 后由 FastAPI 托管
- **LLM**: OpenAI-compatible Provider Factory（支持 GPT 中转 / DashScope / OpenAI / 本地兼容端点）
- **Embedding / 检索**: Embedding 可选，支持 dense Milvus 或 BM25-only 降级
- **向量库**: Milvus（embedding disabled 时非必需）
- **工具协议**: MCP (Model Context Protocol)

## 🚀 快速开始

### 环境要求
- Python 3.10+
- Chat LLM Key：支持 OpenAI-compatible 中转、OpenAI、DashScope。
- Embedding Key：可选；没有 embedding 时设 `EMBEDDING_PROVIDER=disabled`，RAG 会走 BM25-only + parent-child 降级。

### 安装和启动

#### Linux/macOS 环境

```bash
# 1. 克隆项目
git clone <repository_url>
cd super_biz_agent_py

# 2. 安装依赖（推荐使用 uv）
# 方式 1: 使用 uv（推荐，更快）
pip install uv
uv venv
source .venv/bin/activate
uv pip install -e .

# 方式 2: 使用 pip
pip install -e .

# 3. 编辑配置文件
# 有 GPT 中转但没有 embedding 时，填 LLM_*，并设置 EMBEDDING_PROVIDER=disabled
cp .env.example .env
vim .env  # 或使用其他编辑器

# 4. 单端口演示（Milvus + MCP + FastAPI 托管 static 前端 + 上传文档）
make one

# 5. 后台启动/重启已有环境
make start
```

#### Windows 环境（PowerShell/CMD）

如果Windows 不支持 `make` 命令，可以手动执行以下步骤以启动服务：

```powershell
# 1. 克隆项目
git clone <repository_url>
cd super_biz_agent_py

# 2. 创建虚拟环境并安装依赖
# 方式 1: 使用 uv（推荐，更快）
pip install uv
# 创建虚拟环境
uv venv
# 激活虚拟环境
.venv\Scripts\activate
# 安装所有依赖
uv pip install -e .

# 方式 2: 使用 pip
python -m venv .venv
.venv\Scripts\activate
pip install -e .

# 3. 编辑配置文件
# 使用记事本或其他编辑器打开 .env 文件；有 GPT 中转则填 LLM_*，无 embedding 则设 EMBEDDING_PROVIDER=disabled
copy .env.example .env
notepad .env

# 4. 启动 Docker Desktop
# 确保 Docker Desktop 已安装并正在运行

# 5. 启动 Milvus 向量数据库（Docker Compose）
docker compose -f vector-database.yml up -d

# 6. 等待 Milvus 启动完成（约 5-10 秒）
timeout /t 10

# 7. 启动 MCP 服务
# 启动 CLS 日志查询服务（新开一个 PowerShell 窗口）
python mcp_servers/cls_server.py

# 启动 Monitor 监控服务（新开一个 PowerShell 窗口）
python mcp_servers/monitor_server.py

# 8. 启动 FastAPI 主服务（新开一个 PowerShell 窗口）
# 注意：日志会自动输出到 logs\app_YYYY-MM-DD.log
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900

# 9. 上传文档到向量库（新开一个 PowerShell 窗口）
# 等待服务启动完成后执行
timeout /t 5
python -c "import requests, os, time; [requests.post('http://localhost:9900/api/upload', files={'file': open(f'aiops-docs/{f}', 'rb')}) or time.sleep(1) for f in os.listdir('aiops-docs') if f.endswith('.md')]"
```

**Windows 一键启动脚本**（推荐）

使用启动脚本：

```powershell
# 启动所有服务
.\start-windows.bat

# 停止所有服务
.\stop-windows.bat
```

### 访问服务
- **生产/演示 Web 界面**: http://localhost:9900
- **API 文档**: http://localhost:9900/docs
- **开发态 Vite 前端**: http://localhost:5173（仅前端开发时单独启动）

## 🧭 前端架构

本项目采用“开发态分离、生产态一体托管”的前端架构，详见 `docs/frontend_architecture_decision.md`。

- **开发态**：FastAPI 与 Vite 分离运行。FastAPI 监听 `9900` 提供 `/api/*`，Vite 监听 `5173` 提供 HMR 和前端源码调试；前端请求使用相对路径 `/api/...`，由 Vite dev proxy 转发到 FastAPI。
- **生产态**：在 `frontend/` 执行 `npm run build`，构建产物输出到仓库根目录 `static/`；FastAPI 挂载 `static/`，统一提供 `/`、`/static/*` 和 `/api/*`。
- **单端口演示**：`make one` 保持单端口入口，只暴露 `http://localhost:9900`，不启动独立 Vite dev server；若前端源码变更，先构建刷新 `static/`，再执行 `make one`。

常用前端命令：

```bash
# 开发态：后端 API
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 9900

# 开发态：前端 Vite
cd frontend
npm ci
npm run dev

# 生产态：生成 FastAPI 托管的 static/ 产物
npm run build
```

## 📡 API 接口

### 核心接口

| 功能 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 普通对话 | POST | `/api/chat` | 一次性返回 |
| 流式对话 | POST | `/api/chat_stream` | SSE 流式输出 |
| AIOps 诊断 | POST | `/api/aiops` | 自动故障诊断（流式） |
| 文件上传 | POST | `/api/upload` | 上传并索引文档 |
| 健康检查 | GET | `/health` | 服务状态检查 |
| RAG 调试 | POST | `/api/rag/retrieve_debug` | 返回检索候选、score、channels、parent/child id |

### 使用示例

```bash
# 普通对话
curl -X POST "http://localhost:9900/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"你好"}'

# 流式对话
curl -X POST "http://localhost:9900/api/chat_stream" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"你好"}' \
  --no-buffer

# RAG 检索调试：查看 BM25 / dense / rerank 分数
curl -X POST "http://localhost:9900/api/rag/retrieve_debug" \
  -H "Content-Type: application/json" \
  -d '{"query":"CPU 使用率高怎么排查","mode":"hybrid_parent","top_k":3}'

# AIOps 诊断
curl -X POST "http://localhost:9900/api/aiops" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"session-123"}' \
  --no-buffer
```

## 📊 评测与真实指标

本项目保留可回放的离线评测，避免只靠主观 demo 讲效果。

### RAG 检索评测

```bash
make eval-retrieval
make eval-rag MODE=hybrid_parent
```

输出位置：

- `evals/retrieval_local_report.json`
- `evals/reports/retrieval_hybrid_parent.json`

### AIOps Agent 评测

```bash
make eval-aiops
```

输出位置：

- `evals/reports/aiops_agent_eval.json`

当前离线 trace benchmark 结果：

| 指标 | 数值 | 含义 |
|------|------|------|
| `tool_call_success_rate` | `0.5` | 6 个 case 中包含 3 个故意设计的失败/超时路径，因此不是粉饰后的 100%。 |
| `expected_tool_hit_rate` | `1.0` | 期望调用的工具均在 trace 中出现。 |
| `evidence_coverage` | `1.0` | 报告引用了对应 Evidence ID，包括 `E001-tool_error`。 |
| `hallucination_block_rate` | `1.0` | 无证据时未输出“内存泄漏/CPU 死循环”等未经证实根因。 |
| `insufficient_evidence_rate` | `0.3333` | 日志源缺失、无工具证据等场景明确返回证据不足。 |
| `timeout_rate` | `0.1667` | Prometheus range 查询超时 case 被独立统计。 |
| `avg_latency_ms` | `0.1717` | 离线 trace 评测平均耗时，不包含 LLM/网络调用。 |

说明：AIOps 评测默认是 `offline_trace`，不调用 LLM、不访问真实 MCP/Prometheus，目的是稳定验证“工具证据与报告边界”。真实运行态仍以 `/api/aiops/self-check`、执行轨迹和 Evidence Package 为准。

## 📁 项目结构

```
super_biz_agent_py/
├── app/                                    # 应用核心
│   ├── __init__.py                         # 包初始化（自动加载日志配置）
│   ├── main.py                             # FastAPI 应用入口
│   ├── config.py                           # 配置管理（环境变量、MCP 服务器配置）
│   ├── api/                                # API 路由层
│   │   ├── __init__.py
│   │   ├── chat.py                         # 对话接口（RAG 聊天）
│   │   ├── aiops.py                        # AIOps 接口（故障诊断）
│   │   ├── file.py                         # 文件管理（文档上传）
│   │   └── health.py                       # 健康检查（服务状态）
│   ├── services/                           # 业务服务层
│   │   ├── __init__.py
│   │   ├── rag_agent_service.py            # RAG Agent（LangGraph 状态图）
│   │   ├── aiops_service.py                # AIOps 服务（计划-执行-重规划）
│   │   ├── vector_store_manager.py         # 向量存储管理器
│   │   ├── vector_embedding_service.py     # 向量embedding服务
│   │   ├── vector_index_service.py         # 向量索引服务
│   │   ├── vector_search_service.py        # 向量检索服务
│   │   ├── parent_child_splitter_service.py # Parent-child 文档切分
│   │   ├── bm25_retrieval_service.py       # BM25 sparse 检索
│   │   ├── hybrid_retrieval_service.py     # Hybrid recall + rerank + parent expansion
│   │   ├── rag_document_store.py           # Parent/child JSONL docstore
│   │   └── document_splitter_service.py    # 旧版 dense 兼容 splitter
│   ├── agent/                              # Agent 模块
│   │   ├── __init__.py
│   │   ├── mcp_client.py                   # MCP 客户端（工具调用）
│   │   └── aiops/                          # AIOps 核心逻辑
│   │       ├── __init__.py
│   │       ├── planner.py                  # 计划制定器
│   │       ├── executor.py                 # 步骤执行器
│   │       ├── replanner.py                # 重规划器
│   │       ├── state.py                    # 状态定义
│   │       └── utils.py                    # 工具函数
│   ├── models/                             # 数据模型层
│   │   ├── __init__.py
│   │   ├── aiops.py                        # AIOps 模型
│   │   ├── document.py                     # 文档模型
│   │   ├── request.py                      # 请求模型
│   │   └── response.py                     # 响应模型
│   ├── tools/                              # Agent 工具集
│   │   ├── __init__.py
│   │   ├── knowledge_tool.py               # 知识库查询工具
│   │   └── time_tool.py                    # 时间工具
│   ├── core/                               # 核心组件
│   │   ├── __init__.py
│   │   ├── llm_factory.py                  # LLM 工厂（模型管理）
│   │   └── milvus_client.py                # Milvus 客户端
│   └── utils/                              # 工具类
│       ├── __init__.py
│       └── logger.py                       # 日志配置（Loguru）
├── frontend/                               # Vite 前端源码（开发态独立运行）
│   ├── package.json                        # npm scripts 与前端依赖
│   ├── vite.config.*                       # Vite 配置，开发态代理 /api 到 FastAPI
│   └── src/                                # 前端业务源码
├── static/                                 # 前端生产构建产物（由 FastAPI 一体托管）
│   ├── index.html                          # 生产首页
│   └── assets/                             # Vite 构建资源；可兼容既有静态文件
├── mcp_servers/                            # MCP 服务器
│   ├── cls_server.py                       # CLS 日志查询服务
│   ├── monitor_server.py                   # 监控数据服务
│   └── README.md                           # MCP 服务说明
├── aiops-docs/                             # 运维知识库（Markdown 文档）
├── logs/                                   # 日志目录（Loguru 自动创建）
│   └── app_YYYY-MM-DD.log                  # 按天轮转的日志文件
├── uploads/                                # 上传文件临时目录
├── volumes/                                # Milvus 数据持久化目录
├── .env                                    # 环境变量配置（需手动创建）
├── Makefile                                # 项目管理命令（Linux/macOS）
├── start-windows.bat                       # Windows 启动脚本
├── stop-windows.bat                        # Windows 停止脚本
├── vector-database.yml                     # Milvus Docker Compose 配置
├── pyproject.toml                          # 项目配置（依赖、元数据）
├── uv.lock                                 # uv 依赖锁定文件
├── pyrightconfig.json                      # Pyright 类型检查配置
└── README.md                               # 项目说明
```

## ⚙️ 配置说明

通过 `.env` 文件配置。当前推荐先用 **GPT 中转 + 无 embedding 降级**：

```bash
# Chat LLM：OpenAI-compatible 中转 / GPT 网关
LLM_PROVIDER=openai_compatible
LLM_API_BASE=https://你的中转地址/v1
LLM_API_KEY=你的中转key
LLM_MODEL=你的模型名

# 没有 embedding key 时：关闭 dense embedding，RAG 走 BM25-only + parent-child
EMBEDDING_PROVIDER=disabled
RAG_RETRIEVAL_MODE=hybrid_parent
```

如果有 DashScope 全家桶：

```bash
LLM_PROVIDER=dashscope
LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=你的 DashScope key
LLM_MODEL=qwen-max

EMBEDDING_PROVIDER=dashscope
EMBEDDING_API_KEY=你的 DashScope key
EMBEDDING_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
RAG_RETRIEVAL_MODE=hybrid_parent
```

如果以后有 OpenAI embedding：

```bash
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=你的 OpenAI key
EMBEDDING_API_BASE=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

注意：`EMBEDDING_DIMENSIONS` 必须和 Milvus collection 维度一致；切换 embedding 维度前建议重建 collection 或全量重传文档。

AIOps 诊断是证据驱动链路，默认 **不降级**：

```bash
AIOPS_STRUCTURED_OUTPUT_METHOD=function_calling
AIOPS_REQUIRE_TOOL_CALL=true
AIOPS_TOOL_CALL_MAX_ROUNDS=3
MCP_CLS_URL=http://localhost:8003/mcp
MCP_MONITOR_URL=http://localhost:8004/mcp
```

这意味着：

- MCP 服务不可用：`/api/aiops` 直接返回 error，不生成假诊断。
- 模型/中转不支持 `structured output`：Planner/Replanner/Report 直接失败。
- 模型/中转不支持 `tool calling` 或不产生 `tool_calls`：Executor 直接失败。


## 📚 RAG Hybrid Parent-Child

当前 RAG 保留旧 `dense` 模式，同时新增 `hybrid` / `hybrid_parent`：

```text
文档 -> parent chunks(章节上下文) -> child chunks(500/80 精准召回)
child -> Milvus dense index + BM25 sparse index
query -> dense recall + BM25 recall -> score fusion/rerank -> parent expansion -> final topK
```

关键说明：

- `dense`：纯向量链路，需要 embedding + Milvus。
- `hybrid`：dense + BM25 多路召回，按 `child_id` 合并后规则重排。
- `hybrid_parent`：在 hybrid 基础上用 `parent_id` 扩展父块上下文，兼顾精准召回和完整答案；当 `EMBEDDING_PROVIDER=disabled` 时会自然降级为 BM25-only + parent expansion。
- Debug API：`POST /api/rag/retrieve_debug` 可直接查看 `dense_score`、`bm25_score`、`rerank_score`、`retrieval_channels`。
- 评估命令：`make eval-rag MODE=hybrid_parent`，报告输出到 `evals/reports/retrieval_hybrid_parent.json`。

详细设计见 `docs/rag_design.md`，改造计划与过程见 `docs/rag_hybrid_parent_child_plan.md` / `docs/rag_hybrid_parent_child_process.md`。

## 🎯 AIOps 智能运维

基于 **Plan-Execute-Replan** 模式实现自动故障诊断。

### 核心特性
- ✅ 自动制定诊断计划（Planner）：使用 `with_structured_output(Plan)`，不再生成默认假计划
- ✅ 智能工具调用（Executor）：模型必须产生 `tool_calls`，并真实调用 CLS/Monitor MCP 工具
- ✅ 动态调整步骤（Replanner）：使用 `with_structured_output(Act)`，失败时显式报错
- ✅ 流式输出诊断过程
- ✅ 生成结构化报告：最终响应来自工具证据，无证据不出报告

### 快速测试

```bash
# 先确保 MCP 工具服务启动
make start-cls
make start-monitor

# 如需重启所有服务：make restart

# 访问 Web 界面，点击"智能运维与诊断工具"
# 或使用 API
curl -X POST "http://localhost:9900/api/aiops" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test"}' \
  --no-buffer
```

### 诊断流程
```
1. Planner 制定计划 → structured output 生成证据收集步骤
2. Executor 执行步骤 → 读取 `AIMessage.tool_calls`，真实调用 MCP 工具并回填 `ToolMessage`
3. Replanner 评估结果 → structured output 决定继续/调整/生成报告
4. 输出诊断报告 → 根因分析 + 运维建议；证据不足时明确写证据不足
```

## 📝 开发指南

### 常用命令

```bash
# 项目管理
make one               # 单端口演示（FastAPI 托管 static 前端）
make init              # 一键初始化（Docker + 服务 + 文档）
make start             # 启动所有服务
make stop              # 停止所有服务
make restart           # 重启所有服务

# 前端开发/构建
make api-dev           # 后端开发态：FastAPI reload，监听 9900
make web-install       # 安装前端依赖
make web-dev           # 前端开发态：Vite HMR，监听 5173
make web-build         # 前端生产态：输出 static/，交给 FastAPI 托管

# 依赖管理
make install-dev       # 安装开发依赖
make sync              # 同步依赖

# Docker 管理
make up                # 启动 Docker 容器
make down              # 停止 Docker 容器

# 代码质量
make format            # 格式化代码
make lint              # 代码检查
```


## 🐛 常见问题

### Windows 环境问题

#### 1. `make` 命令不可用
Windows 不支持 `make` 命令，请使用提供的批处理脚本：
```powershell
# 启动服务
.\start-windows.bat

# 停止服务
.\stop-windows.bat
```

#### 2. PowerShell 执行策略限制
如果遇到 "无法加载文件，因为在此系统上禁止运行脚本" 错误：
```powershell
# 临时允许脚本执行（管理员权限）
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process

# 或者使用 CMD 而不是 PowerShell
cmd
.\start-windows.bat
```

#### 3. 端口被占用（Windows）
```powershell
# 查看占用端口的进程
netstat -ano | findstr :9900

# 结束进程（替换 PID 为实际进程 ID）
taskkill /F /PID <PID>
```

### 通用问题

### API Key 错误
```bash
# 检查模型配置（不要把真实 key 发到公网/截图）
grep -E "^(LLM_PROVIDER|LLM_API_BASE|LLM_MODEL|EMBEDDING_PROVIDER|EMBEDDING_MODEL)=" .env

# DashScope 旧配置也兼容，但推荐新项目使用 LLM_* / EMBEDDING_*
grep -E "^(DASHSCOPE_API_BASE|DASHSCOPE_MODEL|DASHSCOPE_EMBEDDING_MODEL)=" .env
```

如果只有 GPT 中转、没有 embedding key，设置：

```bash
EMBEDDING_PROVIDER=disabled
RAG_RETRIEVAL_MODE=hybrid_parent
```

### Milvus 连接失败
```bash
# 确保本机有 Docker 服务并且已经启动（可以使用 Docker Desktop）

# 检查 Milvus 状态
docker ps | grep milvus

# 重启 Milvus（使用 docker compose）
docker compose -f vector-database.yml restart

# 或者重启单个服务
docker compose -f vector-database.yml restart standalone
```

### 服务无法启动

**Linux/macOS:**
```bash
# 查看服务日志
tail -f logs/app_$(date +%Y-%m-%d).log  # FastAPI 主服务（Loguru 日志）
tail -f mcp_cls.log                      # CLS MCP 服务
tail -f mcp_monitor.log                  # Monitor MCP 服务

# 检查端口占用
lsof -i :9900  # FastAPI
lsof -i :8003  # CLS MCP
lsof -i :8004  # Monitor MCP
```

**Windows:**
```powershell
# 查看服务日志（获取今天的日期）
$today = Get-Date -Format "yyyy-MM-dd"
type logs\app_$today.log  # FastAPI 主服务（Loguru 日志）
type mcp_cls.log          # CLS MCP 服务
type mcp_monitor.log      # Monitor MCP 服务

# 或者查看最新的日志文件
Get-ChildItem logs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content -Tail 50

# 检查端口占用
netstat -ano | findstr :9900  # FastAPI
netstat -ano | findstr :8003  # CLS MCP
netstat -ano | findstr :8004  # Monitor MCP
```

## 📚 参考资源

- [FastAPI 文档](https://fastapi.tiangolo.com/)
- [LangChain 文档](https://python.langchain.com/)
- [LangGraph Plan-Execute](https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/)
- [阿里云 DashScope](https://dashscope.aliyun.com/)
- [MCP 协议](https://modelcontextprotocol.io/)

## 📄 许可证
author： chief

MIT License
