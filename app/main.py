"""FastAPI 应用入口

主应用程序，配置路由、中间件、静态文件等
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api import aiops, chat, file, health, rag
from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.vector_store_manager import vector_store_manager

static_dir = "static"
_SPA_FALLBACK_EXCLUDED_SEGMENTS = {"api", "docs", "openapi.json", "health", "static"}


def _static_index_path() -> Path:
    """返回当前静态首页路径，允许测试 monkeypatch static_dir。"""
    return Path(static_dir) / "index.html"


def _api_welcome() -> dict[str, str]:
    """API-only 模式欢迎信息。"""
    return {
        "message": f"Welcome to {config.app_name} API",
        "version": config.app_version,
        "docs": "/docs",
    }


def _is_spa_fallback_path(path: str) -> bool:
    """判断请求路径是否允许进入 SPA fallback。"""
    first_segment = path.lstrip("/").split("/", 1)[0]
    return first_segment not in _SPA_FALLBACK_EXCLUDED_SEGMENTS


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("=" * 60)
    logger.info(f"🚀 {config.app_name} v{config.app_version} 启动中...")
    logger.info(f"📝 环境: {'开发' if config.debug else '生产'}")
    logger.info(f"🌐 监听地址: http://{config.host}:{config.port}")
    logger.info(f"📚 API 文档: http://{config.host}:{config.port}/docs")

    # 连接 Milvus
    logger.info("🔌 正在连接 Milvus...")
    milvus_manager.connect()
    logger.info("✅ Milvus 连接成功")
    vector_store_manager.initialize()
    logger.info("✅ VectorStore 初始化成功")

    logger.info("=" * 60)

    yield

    # 关闭时执行
    logger.info("🔌 正在关闭 Milvus 连接...")
    milvus_manager.close()
    logger.info(f"👋 {config.app_name} 关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    description="基于 LangChain 的智能oncall运维系统",
    lifespan=lifespan,
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router, tags=["健康检查"])
app.include_router(chat.router, prefix="/api", tags=["对话"])
app.include_router(file.router, prefix="/api", tags=["文件管理"])
app.include_router(aiops.router, prefix="/api", tags=["AIOps智能运维"])
app.include_router(rag.router, prefix="/api", tags=["RAG调试"])

# 挂载静态文件
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    """返回首页"""
    index_path = _static_index_path()
    if index_path.is_file():
        return FileResponse(index_path)
    return _api_welcome()


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    """前端路由刷新时返回 SPA 入口，API/文档/健康检查路径保持 404 语义。"""
    if not _is_spa_fallback_path(full_path):
        raise HTTPException(status_code=404, detail="Not Found")

    index_path = _static_index_path()
    if index_path.is_file():
        return FileResponse(index_path)

    raise HTTPException(status_code=404, detail="Not Found")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app", host=config.host, port=config.port, reload=config.debug, log_level="info"
    )
