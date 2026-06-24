"""
Executor 节点：执行单个步骤
基于 LangGraph 官方教程实现
"""

import asyncio
import json
import re
from collections.abc import Iterable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from loguru import logger

from app.config import config
from app.core.llm_factory import llm_factory
from app.services.chat_trace_service import ChatTraceObserver

from .state import PlanExecuteState
from .utils import (
    AIOpsCapabilityError,
    AIOpsExecutionError,
    assert_non_empty_text,
    format_exception,
    load_aiops_tools_strict,
    message_to_text,
)


_KNOWN_AIOPS_TOOL_NAMES = {
    "get_current_time",
    "retrieve_knowledge",
    "get_current_timestamp",
    "get_region_code_by_name",
    "search_topic_by_service_name",
    "search_log",
    "query_cpu_metrics",
    "query_memory_metrics",
}
_NEGATED_TOOL_PREFIX_RE = re.compile(
    r"(禁止|不要|不得|不能|不可|不应|无需|无须|不需要|别|避免|拒绝)(?:[\s\S]{0,8})$"
)


async def executor(state: PlanExecuteState) -> dict[str, Any]:
    """
    执行节点：执行计划中的下一个步骤。

    AIOps 是证据驱动链路：MCP 不可用、模型无法 tool calling、步骤未产生工具调用，
    都必须显式失败，而不是退回普通文本回答。
    """
    logger.info("=== Executor：执行步骤 ===")

    plan = state.get("plan", [])
    if not plan:
        logger.info("计划为空，跳过执行")
        return {}

    task = plan[0]
    logger.info(f"当前任务: {task}")

    local_tools, mcp_tools = await load_aiops_tools_strict()
    all_tools = local_tools + mcp_tools
    tools_by_name = {getattr(tool, "name", ""): tool for tool in all_tools}

    explicit_tool_names = _extract_explicit_tool_names(task, tools_by_name.keys())
    missing_explicit_tools = [name for name in explicit_tool_names if name not in tools_by_name]
    if missing_explicit_tools:
        raise AIOpsExecutionError(
            "当前步骤显式指定了未加载的工具: "
            f"{', '.join(missing_explicit_tools)}；拒绝改用其他工具生成伪证据。"
        )

    required_tool_names = [name for name in explicit_tool_names if name in tools_by_name]
    required_tool_set = set(required_tool_names)
    execution_tools = (
        [tools_by_name[name] for name in required_tool_names] if required_tool_names else all_tools
    )
    called_required_tool_names: set[str] = set()

    if required_tool_names:
        logger.info(
            "当前步骤检测到显式工具约束: {}；Executor 将只绑定这些工具",
            ", ".join(required_tool_names),
        )

    first_round_tool_choice = (
        "required" if config.aiops_require_tool_call or required_tool_names else "auto"
    )
    try:
        llm = llm_factory.create_chat_model(
            model=config.effective_llm_model,
            temperature=0,
            streaming=False,
        )
        llm_with_required_tools = llm.bind_tools(execution_tools, tool_choice="required")
        llm_with_optional_tools = llm.bind_tools(execution_tools, tool_choice="auto")
    except Exception as exc:
        detail = format_exception(exc)
        logger.error("工具绑定失败: {}", detail, exc_info=True)
        raise AIOpsCapabilityError(
            "当前 LLM/中转不支持 LangChain tool calling/bind_tools，AIOps 执行器无法调用 MCP 工具。"
            f"原始错误: {detail}"
        ) from exc

    trace_observer = ChatTraceObserver()
    tool_events: list[dict[str, Any]] = [
        trace_observer.event(
            "stage",
            f"开始执行 AIOps 步骤：{task}",
            status="started",
            node="executor",
            summary=(
                "Executor 将先请求模型选择必要工具，再把工具返回作为证据继续总结；"
                "这里展示可审计轨迹，不展示隐藏思维链。"
            ),
        )
    ]

    explicit_tool_rule = ""
    if required_tool_names:
        explicit_tool_rule = (
            "\n7. 当前步骤显式指定了这些工具，必须全部调用且只能调用它们："
            f"{', '.join(required_tool_names)}。"
            "\n8. 禁止用 retrieve_knowledge、常识或知识库内容替代上述监控/日志工具证据。"
        )

    messages = [
        SystemMessage(
            content=f"""你是一个严格的 AIOps Executor，负责执行单个计划步骤。

硬性规则：
1. 当前步骤至少调用一次工具获取真实证据，禁止只用常识直接回答。
2. 如果步骤指定了工具，优先调用指定工具。
3. 对日志/监控/主题查询必须使用 MCP 工具。
4. 已经拿到足够工具结果后，必须基于工具返回内容总结当前步骤。
5. 如果工具失败，必须把失败原因作为执行结果的一部分返回。
6. 专注于当前步骤，不要生成最终报告。{explicit_tool_rule}"""
        ),
        HumanMessage(content=f"请执行以下 AIOps 步骤，并调用必要工具获取证据：{task}"),
    ]

    max_rounds = max(1, config.aiops_tool_call_max_rounds)
    tool_call_count = 0

    try:
        for round_index in range(max_rounds):
            missing_required_before_round = _missing_required_tool_names(
                required_tool_names,
                called_required_tool_names,
            )
            force_tool_call = bool(missing_required_before_round) or (
                tool_call_count == 0 and first_round_tool_choice == "required"
            )
            round_tool_choice = "required" if force_tool_call else "auto"
            round_llm = (
                llm_with_required_tools if force_tool_call else llm_with_optional_tools
            )
            logger.info(
                "Executor 第 {} 轮请求 LLM，tool_choice={}",
                round_index + 1,
                round_tool_choice,
            )
            llm_response = await _ainvoke_with_timeout(
                round_llm,
                messages,
                label=f"Executor 第 {round_index + 1} 轮 LLM 请求",
            )
            logger.info("Executor 第 {} 轮 LLM 响应类型: {}", round_index + 1, type(llm_response))

            tool_calls = getattr(llm_response, "tool_calls", None) or []
            tool_events.append(
                trace_observer.event(
                    "stage",
                    f"Executor 第 {round_index + 1} 轮模型决策",
                    status="completed",
                    node="executor",
                    summary=(
                        f"模型请求调用 {len(tool_calls)} 个工具。"
                        if tool_calls
                        else "模型未继续请求工具，将基于已有工具证据总结当前步骤。"
                    ),
                )
            )
            tool_events.extend(trace_observer.events_from_message(llm_response, node_name="executor"))

            if not tool_calls:
                missing_required = _missing_required_tool_names(
                    required_tool_names,
                    called_required_tool_names,
                )
                if missing_required:
                    raise AIOpsCapabilityError(
                        "模型未调用当前步骤显式指定工具: "
                        f"{', '.join(missing_required)}；拒绝继续生成伪诊断结果。"
                    )
                if tool_call_count == 0 and config.aiops_require_tool_call:
                    raise AIOpsCapabilityError(
                        "模型没有产生任何 tool_calls。AIOps 执行器要求模型支持并实际调用工具，"
                        "请更换支持 tool/function calling 的模型或中转。"
                    )
                result = assert_non_empty_text(message_to_text(llm_response), label="Executor 最终输出")
                logger.info("步骤执行完成，工具调用 {} 次，结果长度: {}", tool_call_count, len(result))
                tool_events.append(
                    trace_observer.event(
                        "stage",
                        f"完成 AIOps 步骤：{task}",
                        status="completed",
                        node="executor",
                        summary=trace_observer.truncate_text(
                            trace_observer.mask_secret_text(result)
                        ),
                    )
                )
                return {
                    "plan": plan[1:],
                    "past_steps": [(task, result)],
                    "tool_events": tool_events,
                }

            logger.info("检测到 {} 个工具调用", len(tool_calls))
            invalid_tool_names = _invalid_tool_names(tool_calls, required_tool_set)
            if invalid_tool_names:
                raise AIOpsCapabilityError(
                    "模型调用了当前步骤未指定的工具: "
                    f"{', '.join(invalid_tool_names)}；当前步骤只允许: "
                    f"{', '.join(required_tool_names)}。"
                )
            tool_call_count += len(tool_calls)
            messages.append(llm_response)
            for tool_call in tool_calls:
                tool_name = tool_call.get("name")
                tool_args = tool_call.get("args") or {}
                tool_call_id = tool_call.get("id")
                tool = tools_by_name.get(tool_name)
                tool_status = "success"
                if tool is None:
                    output_text = f"工具不存在: {tool_name}"
                    tool_status = "error"
                    logger.error(output_text)
                else:
                    try:
                        output = await tool.ainvoke(tool_args)
                        output_text = _tool_output_to_text(output)
                        logger.info("工具 {} 调用完成，输出长度: {}", tool_name, len(output_text))
                    except Exception as tool_exc:
                        output_text = f"工具 {tool_name} 调用失败: {format_exception(tool_exc)}"
                        tool_status = "error"
                        logger.error(output_text)

                tool_message = ToolMessage(
                    content=output_text,
                    name=tool_name or "unknown_tool",
                    tool_call_id=tool_call_id or f"missing-{round_index}-{tool_name}",
                    status=tool_status,
                )
                messages.append(tool_message)
                tool_events.extend(trace_observer.events_from_message(tool_message, node_name="executor"))
                if tool_name in required_tool_set:
                    called_required_tool_names.add(tool_name)

            missing_required = _missing_required_tool_names(
                required_tool_names,
                called_required_tool_names,
            )
            if missing_required:
                if round_index == max_rounds - 1:
                    raise AIOpsExecutionError(
                        "当前步骤显式指定工具未全部调用，已调用: "
                        f"{', '.join(sorted(called_required_tool_names)) or '无'}；缺失: "
                        f"{', '.join(missing_required)}。拒绝基于不完整证据生成结论。"
                    )
                logger.warning(
                    "当前步骤仍缺少显式指定工具: {}，继续下一轮工具调用",
                    ", ".join(missing_required),
                )
                messages.append(
                    HumanMessage(
                        content=(
                            "当前步骤还缺少这些显式指定工具证据："
                            f"{', '.join(missing_required)}。"
                            "请继续调用缺失工具，不要总结，不要改用 retrieve_knowledge。"
                        )
                    )
                )
                continue

            if tool_call_count > 0 and not config.aiops_executor_summarize_with_llm:
                result = _build_tool_evidence_summary(task, messages, trace_observer=trace_observer)
                logger.info(
                    "步骤执行完成，工具调用 {} 次，跳过 Executor 二次 LLM 总结，结果长度: {}",
                    tool_call_count,
                    len(result),
                )
                tool_events.append(
                    trace_observer.event(
                        "stage",
                        f"完成 AIOps 步骤：{task}",
                        status="completed",
                        node="executor",
                        summary=trace_observer.truncate_text(
                            trace_observer.mask_secret_text(result)
                        ),
                    )
                )
                return {
                    "plan": plan[1:],
                    "past_steps": [(task, result)],
                    "tool_events": tool_events,
                }

            if round_index == max_rounds - 1:
                final_response = await _ainvoke_with_timeout(
                    llm,
                    [
                        *messages,
                        HumanMessage(content="工具调用轮次已达上限，请基于已有工具结果总结当前步骤证据。"),
                    ],
                    label="Executor 工具轮次上限后的总结 LLM 请求",
                )
                result = assert_non_empty_text(
                    message_to_text(final_response),
                    label="Executor 达到工具轮次上限后的总结",
                )
                logger.info("步骤执行完成，工具调用 {} 次，结果长度: {}", tool_call_count, len(result))
                tool_events.append(
                    trace_observer.event(
                        "stage",
                        f"完成 AIOps 步骤：{task}",
                        status="completed",
                        node="executor",
                        summary=trace_observer.truncate_text(
                            trace_observer.mask_secret_text(result)
                        ),
                    )
                )
                return {
                    "plan": plan[1:],
                    "past_steps": [(task, result)],
                    "tool_events": tool_events,
                }

    except (AIOpsCapabilityError, AIOpsExecutionError):
        raise
    except Exception as exc:
        detail = format_exception(exc)
        logger.error("执行步骤失败: {}", detail, exc_info=True)
        raise AIOpsExecutionError(f"执行步骤失败，当前步骤未完成: {detail}") from exc

    raise AIOpsExecutionError("Executor 未产生执行结果")


def _extract_explicit_tool_names(task: str, available_tool_names: Iterable[str]) -> list[str]:
    """按步骤文本中显式出现的工具名提取必须调用的工具，保持出现顺序。"""
    task_text = task or ""
    candidate_names = set(_KNOWN_AIOPS_TOOL_NAMES)
    candidate_names.update(name for name in available_tool_names if name)

    matches: list[tuple[int, str]] = []
    for name in candidate_names:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        match = re.search(pattern, task_text)
        if match and not _is_negated_tool_reference(task_text, match.start()):
            matches.append((match.start(), name))

    ordered_names: list[str] = []
    seen: set[str] = set()
    for _, name in sorted(matches, key=lambda item: item[0]):
        if name not in seen:
            seen.add(name)
            ordered_names.append(name)
    return ordered_names


def _is_negated_tool_reference(task_text: str, start_index: int) -> bool:
    """识别“禁止/不要/不得使用 tool”这类负向提及，避免误判为必调工具。"""
    prefix = task_text[max(0, start_index - 18) : start_index]
    return bool(_NEGATED_TOOL_PREFIX_RE.search(prefix))


def _missing_required_tool_names(
    required_tool_names: list[str],
    called_required_tool_names: set[str],
) -> list[str]:
    """返回尚未调用的显式指定工具，保持原始顺序。"""
    return [name for name in required_tool_names if name not in called_required_tool_names]


def _invalid_tool_names(tool_calls: list[dict[str, Any]], required_tool_set: set[str]) -> list[str]:
    """当步骤显式指定工具时，拒绝模型调用指定集合之外的工具。"""
    if not required_tool_set:
        return []
    invalid_names = []
    seen: set[str] = set()
    for tool_call in tool_calls:
        name = str(tool_call.get("name") or "")
        if name not in required_tool_set and name not in seen:
            seen.add(name)
            invalid_names.append(name or "<missing>")
    return invalid_names


def _build_tool_evidence_summary(
    task: str,
    messages: list[Any],
    *,
    trace_observer: ChatTraceObserver,
) -> str:
    """直接把本步骤工具结果整理为证据，避免额外 LLM 总结成为阻塞点。"""
    parts = [f"当前步骤：{task}", "", "工具证据："]
    tool_result_count = 0
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        tool_result_count += 1
        tool_name = getattr(message, "name", None) or "unknown_tool"
        tool_status = getattr(message, "status", "success") or "success"
        content = trace_observer.truncate_text(
            trace_observer.mask_secret_text(_tool_output_to_text(getattr(message, "content", ""))),
            2400,
        )
        parts.append(f"{tool_result_count}. {tool_name} [{tool_status}]\n{content}")

    if tool_result_count == 0:
        raise AIOpsExecutionError("工具调用完成但未产生工具结果，拒绝继续生成伪结果")

    return "\n".join(parts).strip()


async def _ainvoke_with_timeout(runnable: Any, messages: list[Any], *, label: str) -> Any:
    """调用 LLM，并用应用级超时防止上游中转长时间悬挂。"""
    timeout_seconds = float(getattr(config, "llm_timeout_seconds", 60.0) or 0)
    if timeout_seconds <= 0:
        return await runnable.ainvoke(messages)
    try:
        return await asyncio.wait_for(runnable.ainvoke(messages), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise AIOpsExecutionError(f"{label} 超时（>{timeout_seconds:g}s），已中止当前步骤") from exc


def _tool_output_to_text(output: Any) -> str:
    """把工具返回值转成可放入 ToolMessage 的文本。"""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        text_blocks = []
        for item in output:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text_blocks.append(item["text"])
            else:
                text_blocks.append(_tool_output_to_text(item))
        return "\n".join(text_blocks)
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except TypeError:
        return str(output)
