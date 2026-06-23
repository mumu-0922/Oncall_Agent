# Repository Guidelines

## 项目结构与模块组织
`app/` 是 FastAPI 后端主体。接口路由放在 `app/api/`，业务逻辑放在 `app/services/`，Agent 编排放在 `app/agent/`，底层集成放在 `app/core/`，数据模型放在 `app/models/`。静态前端资源位于 `static/`，MCP 服务脚本位于 `mcp_servers/`，知识库示例文档位于 `aiops-docs/`。根目录下的 `Makefile`、`pyproject.toml` 和 `vector-database.yml` 负责开发、依赖和本地基础设施。

## 构建、测试与开发命令
推荐先执行 `uv pip install -e ".[dev]"`；也可使用 `pip install -e ".[dev]"`。

- `make init`：启动 Milvus、MCP、FastAPI，并上传 `aiops-docs/` 文档。
- `make dev`：以热重载模式启动本地服务，默认端口 `9900`。
- `make start` / `make stop`：启动或停止后台 MCP 与 API 服务。
- `make format`：执行 Ruff import 修复与格式化。
- `make lint`：检查 `app/` 下代码风格与静态问题。
- `make type-check`：对 `app/` 执行 MyPy 类型检查。
- `make test`：运行 Pytest 并生成覆盖率报告。

## 代码风格与命名约定
项目目标版本为 Python 3.11，统一使用 4 空格缩进。模块、函数、变量使用 `snake_case`，类名使用 `PascalCase`。`pyproject.toml` 已配置 Black、Ruff、isort，行宽限制为 100。新增接口时，尽量让 `app/api/` 保持轻量，把复杂逻辑下沉到 `app/services/` 或 `app/agent/`。

## 测试规范
Pytest 约定测试文件命名为 `tests/test_*.py` 或 `tests/*_test.py`，覆盖率统计范围为 `app/`。当前仓库尚未提交 `tests/` 目录，新增功能时应一并补充测试。建议按源码结构组织测试，例如 `tests/api/test_chat.py`。提交前至少运行一次 `make test`。

## 提交与 Pull Request 规范
现有提交历史较简洁，例如 `init project`。后续提交建议继续使用简短、祈使式、小写风格，例如 `add aiops health check`、`refactor vector search service`。每次提交只聚焦一个主题。PR 需说明行为变化、列出验证步骤、关联 issue；如果修改了 `static/` 下页面，附上截图。

## 安全与配置说明
敏感配置写入 `.env`，由 `app/config.py` 统一加载。不要提交 API Key、日志文件、PID 文件、`htmlcov/` 或其他本地产物。
