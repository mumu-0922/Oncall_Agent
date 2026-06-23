"""健康检查接口"""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager

router = APIRouter()


@router.get("/health")
async def health_check():

    """健康检查接口
    检查服务状态和数据库连接状态

    Returns:
        JSONResponse: 健康检查结果
    """
    # 检查服务基本状态
    health_data: dict[str, Any] = {  # pyright: ignore[reportExplicitAny]
        "service": config.app_name,
        "version": config.app_version,
        "status": "healthy",
        "llm": {
            "provider": config.effective_llm_provider,
            "model": config.effective_llm_model,
        },
        "embedding": {
            "provider": config.effective_embedding_provider,
            "model": config.effective_embedding_model,
            "enabled": config.is_embedding_enabled,
            "dimensions": config.embedding_dimensions,
        },
    }

    # 检查 Milvus 连接状态；embedding disabled 时 Milvus 不是必需依赖。
    if not config.is_embedding_enabled:
        health_data["milvus"] = {
            "status": "skipped",
            "message": "Embedding disabled，Milvus 非必需，RAG 使用 BM25-only 降级",
        }
    else:
        try:
            milvus_healthy = milvus_manager.health_check()
            milvus_status: str = "connected" if milvus_healthy else "disconnected"
            milvus_message: str = "Milvus 连接正常" if milvus_healthy else "Milvus 连接异常"
            health_data["milvus"] = {
                "status": milvus_status,
                "message": milvus_message,
            }
        except Exception as e:
            logger.warning(f"Milvus 健康检查失败: {e}")
            health_data["milvus"] = {
                "status": "error",
                "message": f"Milvus 检查失败: {str(e)}",
            }

    # 判断整体健康状态
    overall_status = "healthy"
    status_code = 200

    # 如果 embedding 启用但 Milvus 不可用，服务不可用；BM25-only 模式不受影响。
    if config.is_embedding_enabled and health_data["milvus"]["status"] != "connected":
        overall_status = "unhealthy"
        status_code = 503
        health_data["error"] = "数据库不可用"

    health_data["status"] = overall_status

    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": "服务运行正常" if overall_status == "healthy" else "服务不可用",
            "data": health_data
        }
    )
