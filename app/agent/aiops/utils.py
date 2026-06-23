"""
AIOps Agent 通用工具函数
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage
from loguru import logger

from app.agent.mcp_client import get_mcp_client_with_retry
from app.tools import get_current_time, retrieve_knowledge

LOCAL_TOOLS = [get_current_time, retrieve_knowledge]


class AIOpsDependencyError(RuntimeError):
    """Raised when a required AIOps runtime dependency is unavailable."""


class AIOpsCapabilityError(RuntimeError):
    """Raised when the configured model/runtime lacks required AIOps capabilities."""


class AIOpsExecutionError(RuntimeError):
    """Raised when an AIOps workflow node cannot complete truthfully."""


def format_tools_description(tools: list) -> str:
    """格式化工具列表为描述文本。"""
    tool_descriptions = []
    for tool in tools:
        if hasattr(tool, "name") and hasattr(tool, "description"):
            tool_descriptions.append(f"- {tool.name}: {tool.description}")
    return "\n".join(tool_descriptions)


def format_exception(error: BaseException) -> str:
    """展开 ExceptionGroup，避免日志只剩 TaskGroup 外壳。"""
    if isinstance(error, BaseExceptionGroup):
        sub_errors = "; ".join(format_exception(exc) for exc in error.exceptions)
        return f"{type(error).__name__}: {error} -> [{sub_errors}]"
    return f"{type(error).__name__}: {error}"


async def load_aiops_tools_strict(*, force_new_mcp_client: bool = False) -> tuple[list, list]:
    """严格加载 AIOps 工具。

    AIOps 诊断依赖日志/监控 MCP 工具。这里不做本地工具降级：MCP 不可用、
    工具为空都直接抛错，让上层返回明确 error，避免继续产出伪诊断报告。
    """
    local_tools = list(LOCAL_TOOLS)
    try:
        mcp_client = await get_mcp_client_with_retry(force_new=force_new_mcp_client)
        mcp_tools = await mcp_client.get_tools()
    except Exception as exc:
        detail = format_exception(exc)
        logger.error("AIOps MCP 工具加载失败: {}", detail)
        raise AIOpsDependencyError(
            "AIOps 诊断需要 CLS/Monitor MCP 服务可用；请先启动 make start-cls "
            f"和 make start-monitor。原始错误: {detail}"
        ) from exc

    if not mcp_tools:
        raise AIOpsDependencyError(
            "AIOps MCP 工具列表为空；请检查 mcp_servers/cls_server.py 与 "
            "mcp_servers/monitor_server.py 是否正常注册工具。"
        )

    logger.info("AIOps 可用工具数量: 本地 {} + MCP {}", len(local_tools), len(mcp_tools))
    return local_tools, mcp_tools


def message_to_text(message: Any) -> str:
    """把 LangChain message / str / dict 统一转为文本。"""
    if isinstance(message, str):
        return message
    if isinstance(message, BaseMessage):
        content = message.content
    else:
        content = getattr(message, "content", message)

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if text:
                    parts.append(str(text))
            elif block is not None:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def assert_non_empty_text(value: str, *, label: str) -> str:
    """验证模型/工具输出非空。"""
    text = (value or "").strip()
    if not text:
        raise AIOpsExecutionError(f"{label} 为空，拒绝继续生成伪结果")
    return text
