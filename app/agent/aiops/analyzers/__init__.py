"""Deterministic AIOps analyzers package.

保持轻量，避免 `app.models.evidence` 导入 AnalyzerFinding 时触发规则模块造成循环导入。
需要运行 analyzer 时请直接导入 `app.agent.aiops.analyzers.rules`。
"""

from app.models.evidence import AnalyzerFinding

__all__ = ["AnalyzerFinding"]
