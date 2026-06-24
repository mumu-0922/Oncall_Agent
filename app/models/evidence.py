"""Evidence Package models for evidence-grounded AIOps reports.

这些模型是 AIOps 诊断报告的事实边界：最终报告只能引用这里沉淀的工具证据；
没有证据时必须明确写“证据不足”，不能让 LLM 用常识补故事。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EvidenceKind = Literal["alert", "metric", "log", "runbook", "tool_error", "tool_result"]
ConfidenceLevel = Literal["low", "medium", "high"]
FindingStatus = Literal["normal", "warning", "critical", "unknown"]
FindingSeverity = Literal["info", "warning", "critical"]


class EvidenceItem(BaseModel):
    """一条可追溯证据。"""

    id: str
    kind: EvidenceKind
    source: str = Field(default="", description="数据来源，如 local_wsl:/proc、local_wsl:file")
    tool_name: str = Field(default="", description="产生证据的工具名")
    status: str = Field(default="success", description="工具状态：success/error/completed")
    title: str = Field(default="", description="面向报告的短标题")
    summary: str = Field(default="", description="短摘要，保留真实错误原因")
    raw_excerpt: str = Field(default="", description="截断后的原始工具返回")
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalyzerFinding(BaseModel):
    """一个确定性 analyzer 的结构化发现。"""

    id: str
    analyzer: str
    status: FindingStatus
    severity: FindingSeverity
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    next_queries: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidencePackage(BaseModel):
    """AIOps 报告生成前的结构化证据包。"""

    incident_id: str
    task: str = ""
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    service_names: list[str] = Field(default_factory=list)
    time_range: dict[str, Any] = Field(default_factory=dict)
    alerts: list[EvidenceItem] = Field(default_factory=list)
    metrics: list[EvidenceItem] = Field(default_factory=list)
    logs: list[EvidenceItem] = Field(default_factory=list)
    runbooks: list[EvidenceItem] = Field(default_factory=list)
    tool_errors: list[EvidenceItem] = Field(default_factory=list)
    tool_results: list[EvidenceItem] = Field(default_factory=list)
    findings: list[AnalyzerFinding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = "low"

    def all_items(self) -> list[EvidenceItem]:
        """按报告优先级返回全部证据。"""
        return [
            *self.alerts,
            *self.metrics,
            *self.logs,
            *self.runbooks,
            *self.tool_errors,
            *self.tool_results,
        ]

    @property
    def evidence_count(self) -> int:
        return len(self.all_items())

    @property
    def actionable_evidence_count(self) -> int:
        """监控/日志/告警才算可用于判断当前环境的事实证据。"""
        return len(self.alerts) + len(self.metrics) + len(self.logs)

    @property
    def has_actionable_evidence(self) -> bool:
        return self.actionable_evidence_count > 0

    @property
    def has_tool_errors(self) -> bool:
        return bool(self.tool_errors)

    def to_prompt_dict(self) -> dict[str, Any]:
        """给 LLM 的紧凑结构，避免塞入完整原始日志。"""
        return {
            "incident_id": self.incident_id,
            "task": self.task,
            "generated_at": self.generated_at,
            "service_names": self.service_names,
            "time_range": self.time_range,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "actionable_evidence_count": self.actionable_evidence_count,
            "limitations": self.limitations,
            "alerts": [item.model_dump(mode="json") for item in self.alerts],
            "metrics": [item.model_dump(mode="json") for item in self.metrics],
            "logs": [item.model_dump(mode="json") for item in self.logs],
            "runbooks": [item.model_dump(mode="json") for item in self.runbooks],
            "tool_errors": [item.model_dump(mode="json") for item in self.tool_errors],
            "tool_results": [item.model_dump(mode="json") for item in self.tool_results],
            "findings": [finding.model_dump(mode="json") for finding in self.findings],
        }

    def to_prompt_markdown(self) -> str:
        """生成可读证据包，供最终报告 prompt 使用。"""
        lines = [
            "# Evidence Package",
            f"- incident_id: {self.incident_id}",
            f"- confidence: {self.confidence}",
            f"- evidence_count: {self.evidence_count}",
            f"- actionable_evidence_count: {self.actionable_evidence_count}",
        ]
        if self.service_names:
            lines.append(f"- service_names: {', '.join(self.service_names)}")
        if self.time_range:
            lines.append(f"- time_range: {self.time_range}")
        if self.limitations:
            lines.append("\n## Limitations")
            lines.extend(f"- {item}" for item in self.limitations)
        if self.findings:
            lines.append("\n## Analyzer Findings")
            for finding in self.findings:
                refs = ", ".join(finding.evidence_refs) if finding.evidence_refs else "no-evidence-ref"
                lines.append(
                    f"- [{finding.id}] {finding.analyzer} {finding.status}/{finding.severity} "
                    f"refs={refs}: {finding.summary}"
                )

        sections = [
            ("Alerts", self.alerts),
            ("Metrics", self.metrics),
            ("Logs", self.logs),
            ("Runbooks", self.runbooks),
            ("Tool Errors", self.tool_errors),
            ("Other Tool Results", self.tool_results),
        ]
        for title, items in sections:
            if not items:
                continue
            lines.append(f"\n## {title}")
            for item in items:
                source = f" source={item.source}" if item.source else ""
                lines.append(
                    f"- [{item.id}] {item.tool_name or item.kind} {item.status}{source}: "
                    f"{item.summary or item.title}"
                )
        return "\n".join(lines).strip()
