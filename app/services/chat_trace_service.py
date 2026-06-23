"""聊天执行轨迹提取与脱敏。

该模块只输出可审计执行轨迹：阶段、工具调用、工具结果摘要。
它不会输出模型隐藏 chain-of-thought。
"""

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

TRACE_ARGS_PREVIEW_LIMIT = 1000
TRACE_RESULT_PREVIEW_LIMIT = 1400
TRACE_TEXT_PREVIEW_LIMIT = 1200
TRACE_LIST_LIMIT = 20
TRACE_DICT_LIMIT = 30
SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|cookie|credential|"
    r"access[_-]?key|refresh[_-]?token|private[_-]?key)",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(sk-[a-z0-9][a-z0-9_-]{8,}|ak-[a-z0-9][a-z0-9_-]{8,}|"
    r"bearer\s+[a-z0-9._~+/=-]{10,})\b"
)


class ChatTraceObserver:
    """从 LangGraph stream / message 中提取前端可展示的执行轨迹。"""

    def __init__(self) -> None:
        self.seen_tool_calls: set[str] = set()
        self.seen_tool_results: set[str] = set()
        self.tool_call_names: dict[str, str] = {}

    def start_event(self) -> dict[str, Any]:
        return self.event(
            "stage",
            "收到问题，进入 Agent 处理",
            status="started",
            node="agent",
            summary="展示的是执行轨迹摘要，不包含模型隐藏思维链。",
        )

    def complete_event(self, duration_ms: int) -> dict[str, Any]:
        return self.event(
            "stage",
            "生成最终回答",
            status="completed",
            node="agent",
            duration_ms=duration_ms,
        )

    def empty_result_event(self, duration_ms: int) -> dict[str, Any]:
        return self.event(
            "stage",
            "Agent 返回空结果",
            status="completed",
            node="agent",
            duration_ms=duration_ms,
        )

    def event(
        self,
        kind: str,
        title: str,
        *,
        status: str = "info",
        node: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "kind": kind,
            "title": title,
            "status": status,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        if node:
            event["node"] = node
        for key, value in extra.items():
            if value is not None:
                event[key] = value
        return event

    def sanitize(self, value: Any, *, depth: int = 0) -> Any:
        """递归脱敏并压缩工具参数/结果中的可展示字段。"""
        if depth >= 5:
            return self.truncate_text(self.mask_secret_text(str(value)), 160)

        if isinstance(value, dict):
            return self._sanitize_dict(value, depth=depth)
        if isinstance(value, (list, tuple, set)):
            return self._sanitize_list(value, depth=depth)
        if isinstance(value, BaseMessage):
            return {"type": type(value).__name__, "content": self.message_to_text(value, limit=240)}
        if isinstance(value, str):
            return self.truncate_text(self.mask_secret_text(value), TRACE_TEXT_PREVIEW_LIMIT)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self.truncate_text(self.mask_secret_text(str(value)), 400)

    def json_preview(self, value: Any, *, limit: int = TRACE_ARGS_PREVIEW_LIMIT) -> str:
        try:
            text = json.dumps(self.sanitize(value), ensure_ascii=False, indent=2, default=str)
        except TypeError:
            text = str(self.sanitize(value))
        return self.truncate_text(text, limit)

    def message_to_text(self, message: Any, *, limit: int = TRACE_RESULT_PREVIEW_LIMIT) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            text = content
        else:
            try:
                text = json.dumps(content, ensure_ascii=False, default=str)
            except TypeError:
                text = str(content)
        return self.truncate_text(self.mask_secret_text(text), limit)

    def events_from_message(self, message: Any, *, node_name: str) -> list[dict[str, Any]]:
        events = self._tool_call_events_from_message(message, node_name=node_name)
        if isinstance(message, ToolMessage):
            result_event = self._tool_result_event_from_message(message, node_name=node_name)
            if result_event:
                events.append(result_event)
        return events

    def events_from_update(self, update_payload: Any) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for node_name, message in self._iter_update_messages(update_payload):
            events.extend(self.events_from_message(message, node_name=node_name))
        return events

    def extract_trace_from_messages(self, messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for message in messages:
            events.extend(self.events_from_message(message, node_name="agent"))
        return events

    def extract_ai_text(self, message: Any) -> str:
        """提取可展示回答文本，过滤工具调用与 reasoning block。"""
        if not isinstance(message, AIMessage) and type(message).__name__ not in (
            "AIMessageChunk",
            "AIMessage",
        ):
            return ""
        if getattr(message, "tool_calls", None):
            return ""

        content_blocks = getattr(message, "content_blocks", None)
        if content_blocks and isinstance(content_blocks, list):
            return "".join(
                str(block.get("text", ""))
                for block in content_blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )

        content = getattr(message, "content", "")
        return content if isinstance(content, str) else ""

    def truncate_text(self, text: str, limit: int = TRACE_TEXT_PREVIEW_LIMIT) -> str:
        if len(text) <= limit:
            return text
        return f"{text[:limit]}...（已截断，原长 {len(text)} 字符）"

    def mask_secret_text(self, text: str) -> str:
        return SECRET_VALUE_RE.sub("***REDACTED***", text)

    def _sanitize_dict(self, value: dict[Any, Any], *, depth: int) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= TRACE_DICT_LIMIT:
                sanitized["..."] = f"已省略 {len(value) - TRACE_DICT_LIMIT} 个字段"
                break
            key_text = str(key)
            sanitized[key_text] = (
                "***REDACTED***"
                if SENSITIVE_KEY_RE.search(key_text)
                else self.sanitize(item, depth=depth + 1)
            )
        return sanitized

    def _sanitize_list(self, value: Sequence[Any] | set[Any], *, depth: int) -> list[Any]:
        seq = list(value)
        sanitized = [self.sanitize(item, depth=depth + 1) for item in seq[:TRACE_LIST_LIMIT]]
        if len(seq) > TRACE_LIST_LIMIT:
            sanitized.append(f"... 已省略 {len(seq) - TRACE_LIST_LIMIT} 项")
        return sanitized

    def _normalize_tool_call(self, tool_call: Any) -> dict[str, Any]:
        if isinstance(tool_call, dict):
            return {
                "id": tool_call.get("id") or tool_call.get("tool_call_id"),
                "name": tool_call.get("name") or tool_call.get("tool_name"),
                "args": tool_call.get("args")
                or tool_call.get("arguments")
                or tool_call.get("input")
                or {},
            }

        function = getattr(tool_call, "function", None)
        raw_args = getattr(tool_call, "args", None)
        if raw_args is None and function is not None:
            raw_args = getattr(function, "arguments", None)
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                raw_args = {"raw": raw_args}
        return {
            "id": getattr(tool_call, "id", None) or getattr(tool_call, "tool_call_id", None),
            "name": getattr(tool_call, "name", None)
            or getattr(tool_call, "tool_name", None)
            or getattr(function, "name", None),
            "args": raw_args or {},
        }

    def _tool_call_events_from_message(
        self,
        message: Any,
        *,
        node_name: str,
    ) -> list[dict[str, Any]]:
        tool_calls = getattr(message, "tool_calls", None) or []
        events: list[dict[str, Any]] = []
        for raw_tool_call in tool_calls:
            tool_call = self._normalize_tool_call(raw_tool_call)
            tool_name = tool_call.get("name") or "unknown_tool"
            tool_call_id = tool_call.get("id") or f"{tool_name}:{len(self.seen_tool_calls)}"
            if tool_call_id in self.seen_tool_calls:
                continue
            self.seen_tool_calls.add(tool_call_id)
            self.tool_call_names[tool_call_id] = tool_name
            events.append(
                self.event(
                    "tool_call",
                    f"调用工具 {tool_name}",
                    status="started",
                    node=node_name,
                    tool=tool_name,
                    call_id=tool_call_id,
                    args=self.sanitize(tool_call.get("args", {})),
                    args_preview=self.json_preview(tool_call.get("args", {})),
                )
            )
        return events

    def _tool_result_event_from_message(
        self,
        message: ToolMessage,
        *,
        node_name: str,
    ) -> dict[str, Any] | None:
        tool_call_id = getattr(message, "tool_call_id", None) or getattr(message, "id", None)
        result_key = str(tool_call_id or id(message))
        if result_key in self.seen_tool_results:
            return None
        self.seen_tool_results.add(result_key)

        tool_name = (
            getattr(message, "name", None)
            or self.tool_call_names.get(str(tool_call_id))
            or "unknown_tool"
        )
        summary, metadata = self._summarize_tool_result(tool_name, message)
        status = "error" if (getattr(message, "status", "success") or "success") == "error" else "completed"
        return self.event(
            "tool_result",
            f"工具 {tool_name} 返回",
            status=status,
            node=node_name,
            tool=tool_name,
            call_id=tool_call_id,
            summary=summary,
            metadata=metadata,
        )

    def _summarize_tool_result(self, tool_name: str, message: ToolMessage) -> tuple[str, dict[str, Any]]:
        text = self.message_to_text(message)
        metadata: dict[str, Any] = {}
        if tool_name == "retrieve_knowledge":
            refs = re.findall(r"【参考资料\s*\d+】", text)
            sources = re.findall(r"来源:\s*(.+)", text)
            if refs:
                metadata["documents"] = len(refs)
            if sources:
                metadata["sources"] = sources[:5]
        return text.strip() or "工具返回为空。", metadata

    def _iter_update_messages(self, update_payload: Any):
        if not isinstance(update_payload, dict):
            return
        for node_name, node_update in update_payload.items():
            messages = (
                node_update.get("messages")
                if isinstance(node_update, dict)
                else getattr(node_update, "messages", None)
            )
            if messages is None:
                continue
            if not isinstance(messages, list):
                messages = [messages]
            for message in messages:
                yield str(node_name), message
