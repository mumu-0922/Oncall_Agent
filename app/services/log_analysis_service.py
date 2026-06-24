"""确定性日志摘要与错误指纹聚合。

该模块不调用 LLM，只对工具返回的结构化日志做噪声过滤、分类和指纹聚合。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_LONG_ID_RE = re.compile(r"\b\d{4,}\b")
_REQUEST_ID_RE = re.compile(
    r"(?i)\b(request[_-]?id|trace[_-]?id|span[_-]?id|task[_-]?id|session[_-]?id)"
    r"\s*[=:]\s*['\"]?[^'\"\s,;]+"
)
_JSON_QUERY_RE = re.compile(r"""["']query["']\s*:\s*["'][^"']*(ERROR|WARN|timeout)[^"']*["']""", re.I)
_HTTP_5XX_RE = re.compile(r"\b(?:HTTP[/ ]?)?5\d{2}\b", re.I)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_NOISE_MARKERS = (
    "调用方法",
    "参数信息",
    "返回状态: SUCCESS",
    "返回结果摘要",
    "search_local_logs 调用成功",
    "MCP 工具 search_local_logs 调用成功",
    "调用 MCP 工具: search_local_logs",
)

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("traceback", re.compile(r"traceback|exception|stack trace", re.I)),
    ("timeout", re.compile(r"time[ -]?out|timed out|超时|TimeoutError|ConnectTimeout", re.I)),
    ("connection", re.compile(r"connection refused|connecterror|connect timeout|连接失败|连接拒绝", re.I)),
    ("http_5xx", _HTTP_5XX_RE),
    ("oom_memory", re.compile(r"\boom\b|out of memory|memoryerror|内存不足|memory", re.I)),
    ("error", re.compile(r"\berror\b|错误|失败", re.I)),
    ("warn", re.compile(r"\bwarn(?:ing)?\b|警告", re.I)),
)


class LogAnalysisService:
    """对 search_local_logs 返回的日志做确定性摘要。"""

    def analyze(self, logs: list[dict[str, Any]] | None, *, max_samples: int = 5) -> dict[str, Any]:
        entries = [item for item in logs or [] if isinstance(item, dict)]
        raw_count = len(entries)
        noise_entries: list[dict[str, Any]] = []
        signal_entries: list[dict[str, Any]] = []
        category_counter: Counter[str] = Counter()
        fingerprint_counter: Counter[str] = Counter()
        fingerprint_examples: dict[str, dict[str, Any]] = {}
        by_file: Counter[str] = Counter()

        for entry in entries:
            message = self.clean_text(str(entry.get("message") or ""))
            if self.is_noise(message):
                noise_entries.append(entry)
                continue

            categories = self.classify(message, level=str(entry.get("level") or ""))
            if not categories:
                noise_entries.append(entry)
                continue

            normalized_entry = {
                "timestamp": entry.get("timestamp"),
                "level": entry.get("level"),
                "file": entry.get("file"),
                "message": message,
                "categories": categories,
            }
            signal_entries.append(normalized_entry)
            by_file.update([str(entry.get("file") or "<unknown>")])
            category_counter.update(categories)
            fingerprint = self.fingerprint(message)
            fingerprint_counter.update([fingerprint])
            fingerprint_examples.setdefault(fingerprint, normalized_entry)

        top_fingerprints = [
            {
                "fingerprint": fingerprint,
                "count": count,
                "example": fingerprint_examples[fingerprint],
            }
            for fingerprint, count in fingerprint_counter.most_common(5)
        ]
        sampled_evidence = signal_entries[:max_samples]
        summary = self._summary(raw_count, len(signal_entries), len(noise_entries), category_counter)

        return {
            "raw_count": raw_count,
            "signal_count": len(signal_entries),
            "noise_count": len(noise_entries),
            "categories": dict(category_counter),
            "top_fingerprints": top_fingerprints,
            "sampled_evidence": sampled_evidence,
            "by_file": dict(by_file),
            "summary": summary,
            "recommended_next_actions": self._recommended_next_actions(category_counter, top_fingerprints),
        }

    def clean_text(self, message: str) -> str:
        """清理日志文本里的终端控制符，避免影响分类、指纹与报告展示。"""
        return _ANSI_RE.sub("", message)

    def is_noise(self, message: str) -> bool:
        text = self.clean_text(message).strip()
        if not text:
            return True
        if any(marker in text for marker in _NOISE_MARKERS):
            return True
        if _JSON_QUERY_RE.search(text):
            return True
        if text in {"{", "}", "[", "]"}:
            return True
        return False

    def classify(self, message: str, *, level: str = "") -> list[str]:
        text = self.clean_text(f"{level} {message}")
        categories = [name for name, pattern in _CATEGORY_PATTERNS if pattern.search(text)]
        # 去重但保序，方便报告稳定。
        seen: set[str] = set()
        ordered: list[str] = []
        for category in categories:
            if category not in seen:
                seen.add(category)
                ordered.append(category)
        return ordered

    def fingerprint(self, message: str) -> str:
        text = self.clean_text(message).strip()
        text = _TIMESTAMP_RE.sub("<ts>", text)
        text = _REQUEST_ID_RE.sub(r"\1=<id>", text)
        text = _UUID_RE.sub("<uuid>", text)
        text = _HEX_RE.sub("<hex>", text)
        text = _LONG_ID_RE.sub("<num>", text)
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"([=:])\s*['\"]?[^'\"\s,;]{24,}", r"\1<value>", text)
        return text[:300]

    def _summary(
        self,
        raw_count: int,
        signal_count: int,
        noise_count: int,
        categories: Counter[str],
    ) -> str:
        if raw_count == 0:
            return "未返回日志记录"
        if signal_count == 0:
            return f"未发现明确异常证据；原始 {raw_count} 条，其中噪声/低信号 {noise_count} 条"
        category_text = "，".join(f"{name} {count} 条" for name, count in categories.most_common())
        return f"发现 {signal_count} 条有效异常信号；过滤噪声 {noise_count} 条；分类：{category_text}"

    def _recommended_next_actions(
        self,
        categories: Counter[str],
        top_fingerprints: list[dict[str, Any]],
    ) -> list[str]:
        if not top_fingerprints:
            return ["未发现明确异常证据，建议继续观察 self-check 与健康检查结果。"]

        actions: list[str] = []
        if categories.get("timeout"):
            actions.append("存在 timeout 信号：优先检查 LLM 网关、MCP endpoint、上游网络和请求超时配置。")
        if categories.get("connection"):
            actions.append("存在连接失败信号：检查服务进程、端口监听、防火墙和 MCP URL 配置。")
        if categories.get("http_5xx"):
            actions.append("存在 HTTP 5xx 信号：查看对应服务日志和最近部署变更。")
        if categories.get("oom_memory"):
            actions.append("存在内存/OOM 信号：检查进程 RSS、容器限制、GC/堆和内存趋势。")
        if categories.get("traceback"):
            actions.append("存在异常堆栈：按 top_fingerprints 中的首个堆栈指纹定位代码路径。")
        if not actions:
            actions.append("存在 ERROR/WARN 信号：按 top_fingerprints 聚合结果查看重复错误。")
        return actions


log_analysis_service = LogAnalysisService()
