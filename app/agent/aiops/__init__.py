"""
通用 Plan-Execute-Replan 框架
基于 LangGraph 官方教程实现
"""

from importlib import import_module
from typing import Any

__all__ = [
    "PlanExecuteState",
    "planner",
    "executor",
    "replanner",
]


def __getattr__(name: str) -> Any:
    """Lazy public exports.

    ``app.agent.aiops.analyzers.rules`` is used by offline evals and should not
    initialize Planner/Replanner/RAG side effects just because the parent
    package was imported. Keep the public API stable while loading heavy nodes
    only when callers actually request them.
    """
    if name == "PlanExecuteState":
        return import_module(".state", __name__).PlanExecuteState
    if name in {"planner", "executor", "replanner"}:
        return getattr(import_module(f".{name}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
