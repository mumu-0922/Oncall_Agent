"""AIOps 自诊断服务。

该服务只做确定性探测，不调用 LLM，不生成推测性结论。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from app.agent.aiops.utils import format_exception, load_aiops_tools_strict
from app.config import config
from app.core.milvus_client import milvus_manager

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+"),
    re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(bearer\s+)?[^'\"\s,}]+"),
)


class AIOpsSelfCheckService:
    """确定性自诊断：检查运行时、MCP、工具、日志、监控配置。"""

    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def run(self) -> dict[str, Any]:
        """执行一键自诊断，返回结构化结果与 Markdown 报告。"""
        started = time.monotonic()
        components: list[dict[str, Any]] = []

        components.append(self._runtime_component())
        components.append(self._llm_component())
        components.append(self._embedding_component())
        components.append(self._milvus_component())
        components.append(self._local_log_config_component())
        components.append(await self._mcp_endpoint_component())

        tool_component, tools_by_name = await self._tools_component()
        components.append(tool_component)
        components.append(await self._local_log_tool_component(tools_by_name))
        components.append(await self._prometheus_component())

        status = self._overall_status(components)
        result = {
            "status": status,
            "generated_at": self._now_iso(),
            "took_ms": int((time.monotonic() - started) * 1000),
            "components": components,
        }
        result["report"] = self._render_report(result)
        return result

    def _runtime_component(self) -> dict[str, Any]:
        return self._component(
            "fastapi",
            "ok",
            "FastAPI 当前进程可响应自诊断请求",
            {
                "service": config.app_name,
                "version": config.app_version,
                "debug": config.debug,
                "host": config.host,
                "port": config.port,
            },
        )

    def _llm_component(self) -> dict[str, Any]:
        has_key = not config._is_placeholder_secret(config.effective_llm_api_key)  # noqa: SLF001
        status = "ok" if has_key and config.effective_llm_model else "warn"
        summary = (
            f"LLM 已配置 provider={config.effective_llm_provider}, "
            f"model={config.effective_llm_model or '<empty>'}"
            if status == "ok"
            else "LLM 配置不完整；AIOps 规划/总结可能不可用"
        )
        return self._component(
            "llm",
            status,
            summary,
            {
                "provider": config.effective_llm_provider,
                "model": config.effective_llm_model,
                "base_url": config.effective_llm_api_base,
                "api_key_present": has_key,
                "timeout_seconds": config.llm_timeout_seconds,
                "max_retries": config.llm_max_retries,
                "aiops_structured_output_method": config.aiops_structured_output_method,
                "aiops_require_tool_call": config.aiops_require_tool_call,
            },
        )

    def _embedding_component(self) -> dict[str, Any]:
        if config.is_embedding_enabled:
            return self._component(
                "embedding",
                "ok",
                f"Embedding 已启用 provider={config.effective_embedding_provider}",
                {
                    "provider": config.effective_embedding_provider,
                    "model": config.effective_embedding_model,
                    "dimensions": config.embedding_dimensions,
                },
            )
        return self._component(
            "embedding",
            "skipped",
            "Embedding 未启用；RAG 使用 BM25-only 降级，不影响 AIOps 自诊断",
            {
                "provider": config.effective_embedding_provider,
                "model": config.effective_embedding_model,
                "enabled": False,
                "reason": "EMBEDDING_PROVIDER=disabled 或未配置可用 embedding key",
            },
        )

    def _milvus_component(self) -> dict[str, Any]:
        if not config.is_embedding_enabled:
            return self._component(
                "milvus",
                "skipped",
                "Embedding 未启用，Milvus 非必需依赖",
                {"required": False},
            )
        try:
            healthy = milvus_manager.health_check()
        except Exception as exc:  # pragma: no cover - 单测覆盖正常分支，异常依赖运行环境
            return self._component(
                "milvus",
                "error",
                f"Milvus 健康检查失败: {format_exception(exc)}",
                {"required": True},
            )
        return self._component(
            "milvus",
            "ok" if healthy else "error",
            "Milvus 连接正常" if healthy else "Milvus 连接异常",
            {"required": True},
        )

    def _local_log_config_component(self) -> dict[str, Any]:
        provider = os.getenv("AIOPS_LOG_PROVIDER", "disabled").strip() or "disabled"
        default_service = os.getenv("AIOPS_DEFAULT_SERVICE", "").strip()
        raw_map = os.getenv("AIOPS_SERVICE_LOG_MAP", "").strip()
        log_map = self._json_env_map(raw_map)

        if not log_map:
            return self._component(
                "local_log_config",
                "error",
                "AIOPS_SERVICE_LOG_MAP 为空；不会扫描全盘，也不会返回假日志",
                {
                    "provider": provider,
                    "default_service": default_service,
                    "services": [],
                },
            )

        services: dict[str, list[dict[str, Any]]] = {}
        missing_or_unreadable = 0
        for service_name, raw_paths in log_map.items():
            paths = raw_paths if isinstance(raw_paths, list) else [raw_paths]
            service_paths: list[dict[str, Any]] = []
            for raw_path in paths:
                path = Path(str(raw_path)).expanduser()
                exists = path.is_file()
                readable = exists and os.access(path, os.R_OK)
                if not readable:
                    missing_or_unreadable += 1
                service_paths.append(
                    {
                        "path": str(path),
                        "exists": exists,
                        "readable": readable,
                        "size_bytes": path.stat().st_size if exists else None,
                    }
                )
            services[str(service_name)] = service_paths

        return self._component(
            "local_log_config",
            "warn" if missing_or_unreadable else "ok",
            (
                "本机日志白名单已配置且文件可读"
                if missing_or_unreadable == 0
                else f"本机日志白名单存在 {missing_or_unreadable} 个不可读/不存在路径"
            ),
            {
                "provider": provider,
                "default_service": default_service,
                "services": services,
            },
        )

    async def _mcp_endpoint_component(self) -> dict[str, Any]:
        servers = config.mcp_servers
        checks = await asyncio.gather(
            *(self._probe_mcp_endpoint(name, spec) for name, spec in servers.items()),
            return_exceptions=True,
        )
        details: dict[str, Any] = {}
        has_error = False
        has_warn = False
        for (name, _), check in zip(servers.items(), checks, strict=False):
            if isinstance(check, Exception):
                details[name] = {"status": "error", "error": format_exception(check)}
                has_error = True
                continue
            details[name] = check
            has_error = has_error or check["status"] == "error"
            has_warn = has_warn or check["status"] == "warn"

        status = "error" if has_error else "warn" if has_warn else "ok"
        return self._component(
            "mcp_endpoints",
            status,
            "MCP endpoint 探测完成",
            details,
        )

    async def _probe_mcp_endpoint(self, name: str, spec: dict[str, str]) -> dict[str, Any]:
        url = spec.get("url", "")
        if not url:
            return {"status": "error", "transport": spec.get("transport"), "error": "MCP URL 为空"}
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
        except Exception as exc:
            return {
                "status": "error",
                "transport": spec.get("transport"),
                "url": url,
                "error": format_exception(exc),
            }

        # FastMCP streamable-http 对 GET /mcp 返回 406 属于可达信号。
        accepted_statuses = {200, 400, 405, 406}
        status = "ok" if response.status_code in accepted_statuses else "warn"
        return {
            "status": status,
            "transport": spec.get("transport"),
            "url": url,
            "http_status": response.status_code,
            "note": "HTTP 406 对 streamable-http MCP 属于正常可达响应"
            if response.status_code == 406
            else None,
        }

    async def _tools_component(self) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            local_tools, mcp_tools = await asyncio.wait_for(
                load_aiops_tools_strict(force_new_mcp_client=True),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            return (
                self._component(
                    "aiops_tools",
                    "error",
                    f"AIOps 工具加载失败: {format_exception(exc)}",
                    {"required_tools": self._required_tools(), "tool_names": []},
                ),
                {},
            )

        tools = list(local_tools) + list(mcp_tools)
        tools_by_name = {getattr(tool, "name", ""): tool for tool in tools if getattr(tool, "name", "")}
        missing = [name for name in self._required_tools() if name not in tools_by_name]
        status = "warn" if missing else "ok"
        return (
            self._component(
                "aiops_tools",
                status,
                "AIOps 工具加载完成" if not missing else f"AIOps 缺少必要工具: {', '.join(missing)}",
                {
                    "local_tool_count": len(local_tools),
                    "mcp_tool_count": len(mcp_tools),
                    "tool_names": sorted(tools_by_name),
                    "required_tools": self._required_tools(),
                    "missing_required_tools": missing,
                },
            ),
            tools_by_name,
        )

    async def _local_log_tool_component(self, tools_by_name: dict[str, Any]) -> dict[str, Any]:
        tool = tools_by_name.get("search_local_logs")
        if tool is None:
            return self._component(
                "search_local_logs",
                "skipped",
                "search_local_logs 工具未加载，无法执行本机日志自检",
                {},
            )

        service_name = os.getenv("AIOPS_DEFAULT_SERVICE", "").strip() or None
        args = {
            "service_name": service_name,
            "query": "level:ERROR OR level:WARN OR timeout",
            "window_minutes": 24 * 60,
            "limit": 10,
        }
        try:
            raw_output = await asyncio.wait_for(tool.ainvoke(args), timeout=self.timeout_seconds)
            payload = self._tool_output_to_payload(raw_output)
        except Exception as exc:
            return self._component(
                "search_local_logs",
                "error",
                f"search_local_logs 调用失败: {format_exception(exc)}",
                {"args": args},
            )

        if not isinstance(payload, dict):
            return self._component(
                "search_local_logs",
                "error",
                "search_local_logs 返回格式无法解析",
                {"raw_type": type(payload).__name__},
            )

        logs = payload.get("logs") if isinstance(payload.get("logs"), list) else []
        sampled_logs = [
            {
                "timestamp": item.get("timestamp"),
                "level": item.get("level"),
                "file": item.get("file"),
                "message": self._mask_secret_text(str(item.get("message", "")))[:500],
            }
            for item in logs[:5]
            if isinstance(item, dict)
        ]
        details = {
            "source": payload.get("source"),
            "matched_service": payload.get("matched_service"),
            "match_reason": payload.get("match_reason"),
            "query": payload.get("query"),
            "total": payload.get("total"),
            "scanned_files": payload.get("scanned_files", []),
            "skipped_files": payload.get("skipped_files", []),
            "limited": payload.get("limited"),
            "took_ms": payload.get("took_ms"),
            "sampled_logs": sampled_logs,
            "error": payload.get("error"),
        }
        if payload.get("error"):
            return self._component("search_local_logs", "error", str(payload["error"]), details)
        return self._component(
            "search_local_logs",
            "ok",
            f"search_local_logs 已返回真实日志证据 {payload.get('total', 0)} 条",
            details,
        )

    async def _prometheus_component(self) -> dict[str, Any]:
        provider = os.getenv("AIOPS_MONITOR_PROVIDER", "disabled").strip().lower() or "disabled"
        prometheus_url = os.getenv("AIOPS_PROMETHEUS_URL", "").strip().rstrip("/")
        alertmanager_url = os.getenv("AIOPS_ALERTMANAGER_URL", "").strip().rstrip("/")
        details: dict[str, Any] = {
            "provider": provider,
            "prometheus_url": prometheus_url or None,
            "alertmanager_url": alertmanager_url or None,
        }

        if provider not in {"prometheus", "prom", "prometheus_vps"}:
            return self._component(
                "prometheus",
                "skipped",
                f"AIOPS_MONITOR_PROVIDER={provider}，未启用 Prometheus 历史指标",
                details,
            )
        if not prometheus_url:
            return self._component(
                "prometheus",
                "warn",
                "AIOPS_MONITOR_PROVIDER=prometheus 但 AIOPS_PROMETHEUS_URL 为空",
                details,
            )

        try:
            probe = await self._probe_prometheus(prometheus_url)
        except Exception as exc:
            probe = {"status": "warn", "error": format_exception(exc)}

        details["probe"] = probe
        if probe.get("status") == "ok":
            return self._component("prometheus", "ok", "Prometheus 可连接，up 查询成功", details)
        return self._component(
            "prometheus",
            "warn",
            f"Prometheus 已配置但探测失败: {probe.get('error') or probe.get('message')}",
            details,
        )

    async def _probe_prometheus(self, base_url: str) -> dict[str, Any]:
        url = urljoin(base_url + "/", "api/v1/query")
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(url, params={"query": "up"})
        if response.status_code != 200:
            return {
                "status": "warn",
                "http_status": response.status_code,
                "error": self._mask_secret_text(response.text[:500]),
            }
        decoded = response.json()
        if decoded.get("status") != "success":
            return {
                "status": "warn",
                "http_status": response.status_code,
                "error": self._mask_secret_text(json.dumps(decoded, ensure_ascii=False)[:500]),
            }
        result = decoded.get("data", {}).get("result", [])
        return {
            "status": "ok",
            "http_status": response.status_code,
            "result_count": len(result) if isinstance(result, list) else None,
        }

    def _tool_output_to_payload(self, output: Any) -> Any:
        if isinstance(output, dict):
            return output
        if isinstance(output, str):
            return self._json_or_text(output)
        if isinstance(output, list):
            text_blocks: list[str] = []
            for item in output:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_blocks.append(item["text"])
                elif isinstance(item, str):
                    text_blocks.append(item)
            if len(text_blocks) == 1:
                return self._json_or_text(text_blocks[0])
            return [self._json_or_text(block) for block in text_blocks]
        return output

    def _json_or_text(self, text: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _json_env_map(self, raw: str) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _render_report(self, result: dict[str, Any]) -> str:
        status_label = {
            "healthy": "健康",
            "degraded": "降级",
            "unhealthy": "异常",
        }.get(result["status"], result["status"])
        lines = [
            "# AIOps 一键自诊断报告",
            "",
            f"- 总体状态：**{status_label}**",
            f"- 生成时间：`{result['generated_at']}`",
            f"- 耗时：`{result['took_ms']} ms`",
            "",
            "## 检查结果",
        ]
        for component in result["components"]:
            icon = self._status_icon(component["status"])
            lines.append(
                f"- {icon} **{component['name']}**：{self._mask_secret_text(component['summary'])}"
            )

        lines.extend(["", "## 关键证据"])
        for component in result["components"]:
            if component["name"] == "search_local_logs":
                details = component.get("details", {})
                lines.append("### search_local_logs")
                lines.append(f"- 数据源：`{details.get('source')}`")
                lines.append(f"- 匹配服务：`{details.get('matched_service')}`")
                lines.append(f"- 查询语句：`{details.get('query')}`")
                lines.append(f"- 返回条数：`{details.get('total')}`")
                scanned_files = details.get("scanned_files") or []
                if scanned_files:
                    lines.append("- 扫描文件：")
                    lines.extend(f"  - `{path}`" for path in scanned_files)
                sampled_logs = details.get("sampled_logs") or []
                if sampled_logs:
                    lines.append("- 样例日志：")
                    for item in sampled_logs[:3]:
                        lines.append(
                            "  - "
                            f"`{item.get('timestamp')}` `{item.get('level')}` "
                            f"`{item.get('file')}` "
                            f"{self._mask_secret_text(str(item.get('message', '')))[:240]}"
                        )
        lines.extend(
            [
                "",
                "## 说明",
                "- 本报告由确定性探测生成，未调用 LLM 编写结论。",
                "- 查不到的项目会显示未配置、不可读、连接失败或降级原因；不会补假数据。",
            ]
        )
        return "\n".join(lines)

    def _component(
        self,
        name: str,
        status: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "status": status,
            "summary": self._mask_secret_text(summary),
            "details": self._mask_nested(details or {}),
        }

    def _mask_nested(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._mask_secret_text(value)
        if isinstance(value, list):
            return [self._mask_nested(item) for item in value]
        if isinstance(value, dict):
            return {key: self._mask_nested(item) for key, item in value.items()}
        return value

    def _mask_secret_text(self, text: str) -> str:
        masked = text
        for pattern in _SECRET_PATTERNS:
            if pattern.groups >= 2:
                masked = pattern.sub(r"\1\2***REDACTED***", masked)
            else:
                masked = pattern.sub("***REDACTED***", masked)
        return masked

    def _overall_status(self, components: list[dict[str, Any]]) -> str:
        statuses = [component["status"] for component in components]
        if "error" in statuses:
            return "unhealthy"
        if "warn" in statuses:
            return "degraded"
        return "healthy"

    def _required_tools(self) -> list[str]:
        return [
            "search_local_logs",
            "query_cpu_metrics",
            "query_memory_metrics",
            "query_metric_instant",
            "list_active_alerts",
        ]

    def _status_icon(self, status: str) -> str:
        return {
            "ok": "✅",
            "warn": "⚠️",
            "error": "❌",
            "skipped": "⏭️",
        }.get(status, "•")

    def _now_iso(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")


aiops_self_check_service = AIOpsSelfCheckService()
