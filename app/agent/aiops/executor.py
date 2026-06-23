"""
Executor 节点：执行单个步骤
基于 LangGraph 官方教程实现
"""

import asyncio
import json
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

    first_round_tool_choice = "required" if config.aiops_require_tool_call else "auto"
    try:
        llm = llm_factory.create_chat_model(
            model=config.effective_llm_model,
            temperature=0,
            streaming=False,
        )
        llm_with_required_tools = llm.bind_tools(all_tools, tool_choice=first_round_tool_choice)
        llm_with_optional_tools = (
            llm.bind_tools(all_tools, tool_choice="auto")
            if first_round_tool_choice != "auto"
            else llm_with_required_tools
        )
    except Exception as exc:
        detail = format_exception(exc)
        logger.error("工具绑定失败: {}", detail, exc_info=True)
        raise AIOpsCapabilityError(
            "当前 LLM/中转不支持 LangChain tool calling/bind_tools，AIOps 执行器无法调用 MCP 工具。"
            f"原始错误: {detail}"
        ) from exc

    tools_by_name = {getattr(tool, "name", ""): tool for tool in all_tools}
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

    messages = [
        SystemMessage(
            content="""你是一个严格的 AIOps Executor，负责执行单个计划步骤。

硬性规则：
1. 当前步骤至少调用一次工具获取真实证据，禁止只用常识直接回答。
2. 如果步骤指定了工具，优先调用指定工具。
3. 对日志/监控/主题查询必须使用 MCP 工具。
4. 已经拿到足够工具结果后，必须基于工具返回内容总结当前步骤。
5. 如果工具失败，必须把失败原因作为执行结果的一部分返回。
6. 专注于当前步骤，不要生成最终报告。"""
        ),
        HumanMessage(content=f"请执行以下 AIOps 步骤，并调用必要工具获取证据：{task}"),
    ]

    max_rounds = max(1, config.aiops_tool_call_max_rounds)
    tool_call_count = 0

    try:
        for round_index in range(max_rounds):
            round_tool_choice = first_round_tool_choice if tool_call_count == 0 else "auto"
            round_llm = (
                llm_with_required_tools if tool_call_count == 0 else llm_with_optional_tools
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
