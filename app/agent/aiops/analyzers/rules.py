"""Deterministic AIOps analyzers.

借鉴 K8sGPT 的 analyzer-first 形态：规则先扫证据，LLM 后解释。
这里不读取外部状态，只消费 EvidenceItem，保证可测试、可回放。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any, Protocol

from app.models.evidence import AnalyzerFinding, EvidenceItem

_CPU_CRITICAL = 90.0
_CPU_WARNING = 80.0
_MEMORY_CRITICAL = 90.0
_MEMORY_WARNING = 80.0
_ERROR_KEYWORDS = ("error", "exception", "traceback", "critical", "fatal", "错误", "异常")


class Analyzer(Protocol):
    name: str

    def analyze(self, items: list[EvidenceItem]) -> list[AnalyzerFinding]:
        """Return deterministic findings from evidence items."""


class CpuHighAnalyzer:
    name = "cpu_high"

    def analyze(self, items: list[EvidenceItem]) -> list[AnalyzerFinding]:
        findings: list[AnalyzerFinding] = []
        for item in _metric_items(items, "cpu"):
            max_value = _metric_max_value(item)
            alert_triggered = _alert_triggered(item)
            if max_value is None and alert_triggered is None:
                findings.append(
                    _finding(
                        analyzer=self.name,
                        status="unknown",
                        severity="warning",
                        summary="CPU 指标缺少 max/p95/data_points，无法判断是否高 CPU。",
                        evidence_refs=[item.id],
                        next_queries=["query_cpu_metrics", "query_metric_range"],
                        metadata={"metric_name": item.metadata.get("metric_name")},
                    )
                )
                continue

            value = max_value or 0.0
            if alert_triggered is True or value >= _CPU_CRITICAL:
                findings.append(
                    _finding(
                        analyzer=self.name,
                        status="critical",
                        severity="critical",
                        summary=f"CPU 指标达到严重阈值，max/p95≈{value:g}%。",
                        evidence_refs=[item.id],
                        next_queries=["search_log", "query_memory_metrics", "query_metric_range"],
                        metadata={"value": value, "threshold": _CPU_CRITICAL},
                    )
                )
            elif value >= _CPU_WARNING:
                findings.append(
                    _finding(
                        analyzer=self.name,
                        status="warning",
                        severity="warning",
                        summary=f"CPU 指标超过预警阈值，max/p95≈{value:g}%。",
                        evidence_refs=[item.id],
                        next_queries=["search_log", "query_metric_range"],
                        metadata={"value": value, "threshold": _CPU_WARNING},
                    )
                )
            else:
                findings.append(
                    _finding(
                        analyzer=self.name,
                        status="normal",
                        severity="info",
                        summary=f"CPU 指标未超过 80% 阈值，max/p95≈{value:g}%。",
                        evidence_refs=[item.id],
                        next_queries=[],
                        metadata={"value": value, "threshold": _CPU_WARNING},
                    )
                )
        return findings


class MemoryHighAnalyzer:
    name = "memory_high"

    def analyze(self, items: list[EvidenceItem]) -> list[AnalyzerFinding]:
        findings: list[AnalyzerFinding] = []
        for item in _metric_items(items, "memory"):
            max_value = _metric_max_value(item)
            alert_triggered = _alert_triggered(item)
            if max_value is None and alert_triggered is None:
                findings.append(
                    _finding(
                        analyzer=self.name,
                        status="unknown",
                        severity="warning",
                        summary="内存指标缺少 max/p95/data_points，无法判断是否高内存。",
                        evidence_refs=[item.id],
                        next_queries=["query_memory_metrics", "query_metric_range"],
                        metadata={"metric_name": item.metadata.get("metric_name")},
                    )
                )
                continue

            value = max_value or 0.0
            if alert_triggered is True or value >= _MEMORY_CRITICAL:
                findings.append(
                    _finding(
                        analyzer=self.name,
                        status="critical",
                        severity="critical",
                        summary=f"内存指标达到严重阈值，max/p95≈{value:g}%。",
                        evidence_refs=[item.id],
                        next_queries=["search_log", "query_cpu_metrics", "query_metric_range"],
                        metadata={"value": value, "threshold": _MEMORY_CRITICAL},
                    )
                )
            elif value >= _MEMORY_WARNING:
                findings.append(
                    _finding(
                        analyzer=self.name,
                        status="warning",
                        severity="warning",
                        summary=f"内存指标超过预警阈值，max/p95≈{value:g}%。",
                        evidence_refs=[item.id],
                        next_queries=["search_log", "query_metric_range"],
                        metadata={"value": value, "threshold": _MEMORY_WARNING},
                    )
                )
            else:
                findings.append(
                    _finding(
                        analyzer=self.name,
                        status="normal",
                        severity="info",
                        summary=f"内存指标未超过 80% 阈值，max/p95≈{value:g}%。",
                        evidence_refs=[item.id],
                        next_queries=[],
                        metadata={"value": value, "threshold": _MEMORY_WARNING},
                    )
                )
        return findings


class LogErrorSpikeAnalyzer:
    name = "log_error_spike"

    def analyze(self, items: list[EvidenceItem]) -> list[AnalyzerFinding]:
        findings: list[AnalyzerFinding] = []
        for item in [item for item in items if item.kind == "log"]:
            error_count = _log_error_count(item)
            total = _safe_int(item.metadata.get("total"), default=0)
            if error_count >= 10:
                status = "critical"
                severity = "critical"
                summary = f"日志窗口内发现大量错误关键词，error_count≈{error_count}，total={total}。"
                next_queries = ["search_log", "query_metric_range"]
            elif error_count > 0:
                status = "warning"
                severity = "warning"
                summary = f"日志窗口内发现错误关键词，error_count≈{error_count}，total={total}。"
                next_queries = ["search_log"]
            else:
                status = "normal"
                severity = "info"
                summary = f"日志窗口内未发现明显 ERROR/exception 关键词，total={total}。"
                next_queries = []
            findings.append(
                _finding(
                    analyzer=self.name,
                    status=status,
                    severity=severity,
                    summary=summary,
                    evidence_refs=[item.id],
                    next_queries=next_queries,
                    metadata={"error_count": error_count, "total": total},
                )
            )
        return findings


class ToolErrorAnalyzer:
    name = "tool_error"

    def analyze(self, items: list[EvidenceItem]) -> list[AnalyzerFinding]:
        findings: list[AnalyzerFinding] = []
        for item in [item for item in items if item.kind == "tool_error"]:
            findings.append(
                _finding(
                    analyzer=self.name,
                    status="unknown",
                    severity="warning",
                    summary=f"工具 {item.tool_name or 'unknown'} 失败或数据源不可用：{item.summary}",
                    evidence_refs=[item.id],
                    next_queries=["检查数据源配置", "重试对应 MCP 工具"],
                    metadata={"tool_name": item.tool_name, "source": item.source},
                )
            )
        return findings


DEFAULT_ANALYZERS: tuple[Analyzer, ...] = (
    CpuHighAnalyzer(),
    MemoryHighAnalyzer(),
    LogErrorSpikeAnalyzer(),
    ToolErrorAnalyzer(),
)


def run_analyzers(items: Iterable[EvidenceItem]) -> list[AnalyzerFinding]:
    """Run all deterministic analyzers and assign stable finding ids."""
    item_list = list(items)
    findings: list[AnalyzerFinding] = []
    for analyzer in DEFAULT_ANALYZERS:
        findings.extend(analyzer.analyze(item_list))
    for index, finding in enumerate(findings, start=1):
        finding.id = f"F{index:03d}-{finding.analyzer}"
    return findings


def _finding(
    *,
    analyzer: str,
    status: str,
    severity: str,
    summary: str,
    evidence_refs: list[str],
    next_queries: list[str],
    metadata: dict[str, Any],
) -> AnalyzerFinding:
    return AnalyzerFinding(
        id="",
        analyzer=analyzer,
        status=status,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        summary=summary,
        evidence_refs=evidence_refs,
        next_queries=next_queries,
        metadata=metadata,
    )


def _metric_items(items: list[EvidenceItem], keyword: str) -> list[EvidenceItem]:
    keyword = keyword.lower()
    return [
        item
        for item in items
        if item.kind == "metric" and keyword in str(item.metadata.get("metric_name") or item.tool_name).lower()
    ]


def _metric_max_value(item: EvidenceItem) -> float | None:
    stats = item.metadata.get("statistics") if isinstance(item.metadata.get("statistics"), dict) else {}
    for key in ("max", "p95", "avg", "average"):
        value = _safe_float(stats.get(key))
        if value is not None:
            return value
    payload = _json_payload(item.raw_excerpt)
    points = payload.get("data_points") if isinstance(payload, dict) else None
    if isinstance(points, list):
        values = [_safe_float(point.get("value")) for point in points if isinstance(point, dict)]
        values = [value for value in values if value is not None]
        if values:
            return max(values)
    return None


def _alert_triggered(item: EvidenceItem) -> bool | None:
    alert_info = item.metadata.get("alert_info") if isinstance(item.metadata.get("alert_info"), dict) else {}
    triggered = alert_info.get("triggered")
    return triggered if isinstance(triggered, bool) else None


def _log_error_count(item: EvidenceItem) -> int:
    payload = _json_payload(item.raw_excerpt)
    logs = payload.get("logs") if isinstance(payload, dict) else None
    if isinstance(logs, list):
        count = 0
        for log in logs:
            if not isinstance(log, dict):
                continue
            level = str(log.get("level") or "").lower()
            message = str(log.get("message") or "").lower()
            if level in {"error", "fatal", "critical"} or any(keyword in message for keyword in _ERROR_KEYWORDS):
                count += 1
        return count
    lowered = item.raw_excerpt.lower()
    return sum(len(re.findall(re.escape(keyword), lowered)) for keyword in _ERROR_KEYWORDS)


def _json_payload(text: str) -> Any:
    cleaned = text.strip()
    if not cleaned:
        return {}
    candidates = [cleaned]
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = cleaned.find(start_char)
        end = cleaned.rfind(end_char)
        if 0 <= start < end:
            candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
