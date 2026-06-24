"""Build Evidence Packages from AIOps execution traces.

该服务借鉴 HolmesGPT / K8sGPT 的 evidence-first 思路，但不依赖第三方仓库代码：
先把工具返回变成结构化证据，再让 Replanner/LLM 基于证据包写报告。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from app.models.evidence import EvidenceItem, EvidenceKind, EvidencePackage
from app.services.chat_trace_service import ChatTraceObserver

_ERROR_MARKERS = (
    "error",
    "exception",
    "traceback",
    "失败",
    "错误",
    "未配置",
    "不可用",
    "禁用",
    "timeout",
    "timed out",
)
_METRIC_TOOLS = {"query_cpu_metrics", "query_memory_metrics", "query_metric_range", "query_metric_instant"}
_LOG_TOOLS = {"search_log", "search_service_logs"}
_RUNBOOK_TOOLS = {"retrieve_knowledge"}
_ALERT_TOOL_HINTS = ("alert", "告警")
_NUMBERED_TOOL_RESULT_RE = re.compile(
    r"(?:^|\n)\s*\d+\.\s+([A-Za-z0-9_\-]+)\s+\[([^\]]+)\]\s*\n(.*?)(?=\n\s*\d+\.\s+[A-Za-z0-9_\-]+\s+\[[^\]]+\]\s*\n|\Z)",
    re.S,
)


class EvidencePackageService:
    """把 LangGraph state / tool trace 转成 Evidence Package。"""

    def __init__(self) -> None:
        self.trace_observer = ChatTraceObserver()

    def build_from_state(self, state: Mapping[str, Any]) -> EvidencePackage:
        """从 AIOps state 构建证据包。"""
        task = str(state.get("input") or "")
        past_steps = state.get("past_steps") or []
        tool_events = state.get("tool_events") or []
        incident_id = self._incident_id(task, past_steps, tool_events)

        items = self._items_from_tool_events(tool_events)
        if not items:
            items = self._items_from_past_steps(past_steps)

        alerts = [item for item in items if item.kind == "alert"]
        metrics = [item for item in items if item.kind == "metric"]
        logs = [item for item in items if item.kind == "log"]
        runbooks = [item for item in items if item.kind == "runbook"]
        tool_errors = [item for item in items if item.kind == "tool_error"]
        tool_results = [item for item in items if item.kind == "tool_result"]

        service_names = self._extract_service_names(items)
        time_range = self._extract_time_range(items)
        limitations = self._extract_limitations(items)
        confidence = self._confidence(alerts=alerts, metrics=metrics, logs=logs, tool_errors=tool_errors)

        return EvidencePackage(
            incident_id=incident_id,
            task=task,
            service_names=service_names,
            time_range=time_range,
            alerts=alerts,
            metrics=metrics,
            logs=logs,
            runbooks=runbooks,
            tool_errors=tool_errors,
            tool_results=tool_results,
            limitations=limitations,
            confidence=confidence,
        )

    def render_insufficient_evidence_report(self, package: EvidencePackage) -> str:
        """在无可用事实证据时，生成确定性报告；不调用 LLM，避免补故事。"""
        lines = [
            "# AIOps 诊断报告",
            "",
            "## 结论",
            "证据不足，拒绝生成未经证实的根因结论。",
            "",
            "## 证据包摘要",
            f"- incident_id: `{package.incident_id}`",
            f"- confidence: `{package.confidence}`",
            f"- actionable_evidence_count: `{package.actionable_evidence_count}`",
            f"- evidence_count: `{package.evidence_count}`",
        ]
        if package.service_names:
            lines.append(f"- service_names: {', '.join(package.service_names)}")

        if package.tool_errors:
            lines.extend(["", "## 工具错误"])
            for item in package.tool_errors:
                lines.append(
                    f"- `{item.tool_name or item.kind}`: {item.summary or item.raw_excerpt or '工具失败，未返回详情'}"
                )

        non_error_items = [item for item in package.all_items() if item.kind != "tool_error"]
        if non_error_items:
            lines.extend(["", "## 已取得但不足以定责的证据"])
            for item in non_error_items:
                lines.append(f"- `{item.id}` `{item.tool_name}`: {item.summary or item.title}")

        if package.limitations:
            lines.extend(["", "## 证据限制"])
            lines.extend(f"- {limitation}" for limitation in package.limitations)

        lines.extend(
            [
                "",
                "## 下一步需要补齐",
                "1. 查询真实活跃告警，例如 `list_active_alerts` 或 Alertmanager。",
                "2. 查询时间范围内的指标曲线，例如 `query_metric_range` / `query_cpu_metrics` / `query_memory_metrics`。",
                "3. 查询同一窗口内的错误日志，例如 `search_log` / `search_service_logs`。",
                "4. 若本地仅有 `/proc` 快照，应明确标记 `history_available=false`，不能伪造历史趋势。",
            ]
        )
        return "\n".join(lines).strip()

    def _items_from_tool_events(self, tool_events: Any) -> list[EvidenceItem]:
        if not isinstance(tool_events, list):
            return []
        items: list[EvidenceItem] = []
        for event in tool_events:
            if not isinstance(event, dict) or event.get("kind") != "tool_result":
                continue
            tool_name = str(event.get("tool") or "unknown_tool")
            status = str(event.get("status") or "completed")
            summary = str(event.get("summary") or "")
            payload = self._json_payload(summary)
            item = self._build_item(
                tool_name=tool_name,
                status=status,
                raw_text=summary,
                payload=payload,
                ordinal=len(items) + 1,
            )
            items.append(item)
        return items

    def _items_from_past_steps(self, past_steps: Any) -> list[EvidenceItem]:
        if not isinstance(past_steps, list):
            return []
        items: list[EvidenceItem] = []
        for step_index, step_payload in enumerate(past_steps, start=1):
            try:
                step, result = step_payload
            except (TypeError, ValueError):
                step, result = f"step-{step_index}", step_payload
            result_text = str(result)
            matches = list(_NUMBERED_TOOL_RESULT_RE.finditer(result_text))
            if not matches:
                payload = self._json_payload(result_text)
                items.append(
                    self._build_item(
                        tool_name="past_step",
                        status="completed",
                        raw_text=result_text,
                        payload=payload,
                        ordinal=len(items) + 1,
                        title=str(step),
                    )
                )
                continue
            for match in matches:
                tool_name, status, raw_text = match.groups()
                payload = self._json_payload(raw_text)
                items.append(
                    self._build_item(
                        tool_name=tool_name,
                        status=status,
                        raw_text=raw_text.strip(),
                        payload=payload,
                        ordinal=len(items) + 1,
                        title=str(step),
                    )
                )
        return items

    def _build_item(
        self,
        *,
        tool_name: str,
        status: str,
        raw_text: str,
        payload: Any,
        ordinal: int,
        title: str = "",
    ) -> EvidenceItem:
        kind = self._classify(tool_name, status, raw_text, payload)
        metadata = self._metadata(payload)
        source = str(metadata.get("source") or "")
        item_id = f"E{ordinal:03d}-{kind}"
        summary = self._summary(kind=kind, tool_name=tool_name, raw_text=raw_text, payload=payload)
        raw_excerpt = self.trace_observer.truncate_text(
            self.trace_observer.mask_secret_text(raw_text.strip()),
            2200,
        )
        return EvidenceItem(
            id=item_id,
            kind=kind,
            source=source,
            tool_name=tool_name,
            status=status,
            title=title,
            summary=summary,
            raw_excerpt=raw_excerpt,
            metadata=metadata,
        )

    def _classify(self, tool_name: str, status: str, raw_text: str, payload: Any) -> EvidenceKind:
        payload_dict = payload if isinstance(payload, dict) else {}
        lower_tool = tool_name.lower()
        lower_raw = raw_text.lower()
        if status == "error" or payload_dict.get("error"):
            return "tool_error"
        if lower_tool in _METRIC_TOOLS or payload_dict.get("metric_name"):
            return "metric"
        if lower_tool in _LOG_TOOLS or "logs" in payload_dict or "topic_id" in payload_dict:
            return "log"
        if lower_tool in _RUNBOOK_TOOLS:
            return "runbook"
        if any(hint in lower_tool for hint in _ALERT_TOOL_HINTS) or "alerts" in payload_dict:
            return "alert"
        if any(marker in lower_raw for marker in _ERROR_MARKERS):
            return "tool_error"
        return "tool_result"

    def _summary(self, *, kind: EvidenceKind, tool_name: str, raw_text: str, payload: Any) -> str:
        payload_dict = payload if isinstance(payload, dict) else {}
        if kind == "tool_error":
            return str(
                payload_dict.get("error")
                or payload_dict.get("message")
                or self.trace_observer.truncate_text(raw_text.strip(), 500)
            )
        if kind == "metric":
            alert_info = payload_dict.get("alert_info") if isinstance(payload_dict.get("alert_info"), dict) else {}
            points = payload_dict.get("data_points") if isinstance(payload_dict.get("data_points"), list) else []
            return (
                f"{payload_dict.get('service_name') or payload_dict.get('matched_service') or 'unknown_service'} "
                f"{payload_dict.get('metric_name') or tool_name}: points={len(points)}, "
                f"source={payload_dict.get('source') or 'unknown'}, "
                f"alert_triggered={alert_info.get('triggered')}"
            )
        if kind == "log":
            logs = payload_dict.get("logs") if isinstance(payload_dict.get("logs"), list) else []
            return (
                f"topic={payload_dict.get('topic_id') or 'unknown'}, "
                f"total={payload_dict.get('total', len(logs))}, "
                f"source={payload_dict.get('source') or 'unknown'}"
            )
        if kind == "alert":
            alerts = payload_dict.get("alerts") if isinstance(payload_dict.get("alerts"), list) else []
            return f"alerts={len(alerts)}, source={payload_dict.get('source') or 'unknown'}"
        if kind == "runbook":
            return self.trace_observer.truncate_text(raw_text.strip(), 500)
        return self.trace_observer.truncate_text(raw_text.strip(), 500)

    def _metadata(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        keys = {
            "service_name",
            "matched_service",
            "metric_name",
            "source",
            "history_available",
            "topic_id",
            "total",
            "start_time",
            "end_time",
            "query",
            "alert_info",
            "statistics",
            "scanned_files",
        }
        return {key: payload[key] for key in keys if key in payload}

    def _json_payload(self, text: str) -> Any:
        cleaned = text.strip()
        if not cleaned:
            return None
        # 工具证据里常见是纯 JSON；若前后有说明，截取第一段对象/数组尝试解析。
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
        return None

    def _extract_service_names(self, items: list[EvidenceItem]) -> list[str]:
        names: list[str] = []
        for item in items:
            for key in ("service_name", "matched_service"):
                value = item.metadata.get(key)
                if value and str(value) not in names:
                    names.append(str(value))
        return names

    def _extract_time_range(self, items: list[EvidenceItem]) -> dict[str, Any]:
        starts = [item.metadata.get("start_time") for item in items if item.metadata.get("start_time")]
        ends = [item.metadata.get("end_time") for item in items if item.metadata.get("end_time")]
        result: dict[str, Any] = {}
        if starts:
            result["start_time"] = min(starts)
        if ends:
            result["end_time"] = max(ends)
        return result

    def _extract_limitations(self, items: list[EvidenceItem]) -> list[str]:
        limitations: list[str] = []
        for item in items:
            if item.metadata.get("history_available") is False:
                limitations.append(
                    f"{item.tool_name} 返回 history_available=false，只能证明当前快照，不能证明历史趋势。"
                )
            if item.kind == "tool_error":
                limitations.append(f"{item.tool_name} 工具失败：{item.summary}")
        if not items:
            limitations.append("没有任何工具证据，不能生成根因结论。")
        # 去重且保序
        deduped: list[str] = []
        for limitation in limitations:
            if limitation not in deduped:
                deduped.append(limitation)
        return deduped

    def _confidence(
        self,
        *,
        alerts: list[EvidenceItem],
        metrics: list[EvidenceItem],
        logs: list[EvidenceItem],
        tool_errors: list[EvidenceItem],
    ) -> str:
        evidence_kinds = sum(1 for bucket in (alerts, metrics, logs) if bucket)
        if evidence_kinds >= 2 and not tool_errors:
            return "high"
        if evidence_kinds >= 1:
            return "medium" if not tool_errors else "low"
        return "low"

    def _incident_id(self, task: str, past_steps: Any, tool_events: Any) -> str:
        basis = json.dumps(
            {"task": task, "past_steps": str(past_steps)[:2000], "tool_events": str(tool_events)[:2000]},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return "aiops-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


evidence_package_service = EvidencePackageService()
