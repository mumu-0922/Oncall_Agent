"""
Replanner 节点：重新规划或生成最终响应
基于 LangGraph 官方教程实现
"""

import asyncio
from textwrap import dedent
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config
from app.core.llm_factory import llm_factory

from .state import PlanExecuteState
from .utils import (
    AIOpsCapabilityError,
    AIOpsExecutionError,
    assert_non_empty_text,
    format_exception,
    format_tools_description,
    load_aiops_tools_strict,
)

_JSON_MODE_FALLBACK_METHOD = "json_mode"
_TIMEOUT_DETAIL_MARKERS = ("timeout", "timed out", "connection", "closed")


class Response(BaseModel):
    """最终响应的格式。"""

    response: str = Field(description="对用户的最终响应，必须是 Markdown 文本")


class Act(BaseModel):
    """重新规划的输出格式。"""

    action: str = Field(
        description="下一步行动，只能是 continue、replan、respond 之一"
    )
    new_steps: list[str] = Field(
        default_factory=list,
        description="action 为 replan 时替换当前剩余计划的新步骤列表",
    )


# Replanner 提示词
replanner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent("""
                作为一个重新规划专家，你需要根据已执行的步骤决定下一步行动。

                可用工具列表（用于制定计划时参考）：

                {tools_description}

                注意：你的职责是制定或调整计划，实际工具调用由 Executor 负责执行。

                你有三个选择（按优先级排序）：

                1. respond - 信息充足，立即生成最终响应【最高优先级】
                   - 已执行步骤已经提供关键监控/日志证据。
                   - 或已执行步骤 >= 3 且足够形成阶段性结论。
                   - 或已执行步骤 >= 5，无论结果是否完美都必须收敛。

                2. continue - 当前计划合理，继续执行【次优先级】
                   - 剩余步骤确实能补足关键证据。

                3. replan - 当前计划有严重问题【最低优先级，谨慎使用】
                   - 新步骤数量必须 <= 当前剩余步骤数。
                   - 总已执行步骤 >= 5 时禁止 replan，只能 respond。

                决策口诀：优先结束 > 保持不变 > 调整计划。信息足够就响应，不追求完美。
            """).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)

# 最终响应生成提示词
response_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent("""
                根据原始任务和已执行步骤的工具证据，生成全面的最终 AIOps 响应。

                响应要求：
                - 使用 Markdown 格式。
                - 必须基于执行历史里的真实工具结果，不要编造。
                - 如果工具结果包含 error/source unavailable/未配置/禁用 mock，必须把该错误作为结论的一部分原样说明。
                - 禁止把知识库经验、示例服务名、示例 PID 当作本次真实环境事实。
                - 若证据不足，要明确写“证据不足”和下一步需要查询的工具/数据。
                - 如果某些步骤失败，要诚实说明失败原因。
                - 对根因结论要区分：已证实 / 高概率 / 待验证。
            """).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)


def _structured_method() -> str:
    method = config.aiops_structured_output_method.strip().lower()
    if method not in {"function_calling", "json_mode", "json_schema"}:
        raise AIOpsCapabilityError(
            "AIops structured output method 非法："
            f"{config.aiops_structured_output_method}；只支持 function_calling/json_mode/json_schema"
        )
    return method


async def replanner(state: PlanExecuteState) -> dict[str, Any]:
    """
    重新规划节点：决定 continue / replan / respond。

    这里不再吞 structured output 错误；如果模型/中转不支持，就显式失败。
    """
    logger.info("=== Replanner：重新规划 ===")

    input_text = state.get("input", "")
    plan = state.get("plan", [])
    past_steps = state.get("past_steps", [])

    logger.info(f"剩余计划步骤: {len(plan)}")
    logger.info(f"已执行步骤: {len(past_steps)}")

    llm = llm_factory.create_chat_model(
        model=config.effective_llm_model,
        temperature=0,
        streaming=False,
    )

    # 强制限制：如果已执行步骤过多，直接生成响应。
    max_steps = 8
    if len(past_steps) >= max_steps:
        logger.warning(f"已执行 {len(past_steps)} 个步骤，超过最大限制 {max_steps}，强制生成最终响应")
        return await _generate_response(state, llm)

    local_tools, mcp_tools = await load_aiops_tools_strict()
    tools_description = format_tools_description(local_tools + mcp_tools)

    steps_summary = "\n".join(
        [f"步骤: {step}\n结果: {str(result)[:600]}..." for step, result in past_steps]
    )

    if plan:
        logger.info("还有剩余计划，评估下一步行动")
        method = _structured_method()
        logger.info("Replanner 使用 structured_output method={}", method)

        act_payload = {
            "messages": [
                ("user", f"原始任务: {input_text}"),
                ("user", f"已执行的步骤:\n{steps_summary}"),
                ("user", f"剩余计划: {', '.join(plan)}"),
                (
                    "user",
                    f"已执行 {len(past_steps)} 个步骤，请严格返回 Act 结构。"
                    "action 只能是 continue/replan/respond。",
                ),
            ],
            "tools_description": tools_description,
        }
        act = await _ainvoke_structured_with_fallback(
            replanner_prompt,
            llm,
            Act,
            method=method,
            payload=act_payload,
            capability_label="AIOps Replanner structured output",
        )

        if isinstance(act, Act):
            action = act.action.strip().lower()
            new_steps = act.new_steps
        elif isinstance(act, dict):
            action = str(act.get("action", "continue")).strip().lower()
            raw_new_steps = act.get("new_steps", [])
            new_steps = raw_new_steps if isinstance(raw_new_steps, list) else []
        else:
            raise AIOpsCapabilityError(f"Replanner structured output 返回未知类型: {type(act)}")

        if action not in {"continue", "replan", "respond"}:
            raise AIOpsExecutionError(f"Replanner 返回非法 action: {action}")

        logger.info(f"Replanner 决策: {action}")

        if action == "respond":
            logger.info("决定生成最终响应")
            return await _generate_response(state, llm)

        if action == "replan":
            if len(past_steps) >= 5:
                logger.warning(f"已执行 {len(past_steps)} 个步骤，禁止重新规划，强制生成响应")
                return await _generate_response(state, llm)
            clean_new_steps = [str(step).strip() for step in new_steps if str(step).strip()]
            if len(clean_new_steps) > len(plan):
                logger.warning("新步骤数 {} > 剩余步骤数 {}，截断", len(clean_new_steps), len(plan))
                clean_new_steps = clean_new_steps[: len(plan)]
            if not clean_new_steps:
                raise AIOpsExecutionError("Replanner 选择 replan 但未提供 new_steps，拒绝假继续")
            logger.info(f"决定调整计划，新步骤数量: {len(clean_new_steps)}")
            return {"plan": clean_new_steps}

        logger.info("决定继续执行当前计划")
        return {}

    logger.info("计划已执行完毕，生成最终响应")
    return await _generate_response(state, llm)


# 生成最终响应时，每个步骤结果的最大字符数，防止 prompt 过大导致超时
_RESPONSE_STEP_RESULT_MAX_CHARS = 3000


async def _generate_response(state: PlanExecuteState, llm: BaseChatModel) -> dict[str, Any]:
    """生成最终响应。"""
    logger.info("生成最终响应...")

    input_text = state.get("input", "")
    past_steps = state.get("past_steps", [])

    if not past_steps:
        raise AIOpsExecutionError("没有任何已执行步骤证据，拒绝生成最终诊断报告")

    # 截断每个步骤结果，防止 execution_history 过大导致 LLM 超时。
    truncated_parts: list[str] = []
    for step, result in past_steps:
        result_str = str(result)
        original_result_len = len(result_str)
        if original_result_len > _RESPONSE_STEP_RESULT_MAX_CHARS:
            result_str = result_str[:_RESPONSE_STEP_RESULT_MAX_CHARS] + (
                f"\n\n[... 结果过长已截断，原始长度 {original_result_len} 字符 ...]"
            )
        truncated_parts.append(f"### 步骤: {step}\n**结果:**\n{result_str}")
    execution_history = "\n\n".join(truncated_parts)
    logger.info("execution_history 总长度: {} 字符，共 {} 个步骤", len(execution_history), len(past_steps))

    method = _structured_method()
    logger.info("Response 使用 structured_output method={}", method)

    response_payload = {
        "messages": [
            ("user", f"原始任务: {input_text}"),
            ("user", f"执行历史:\n{execution_history}"),
            ("user", "请基于以上工具证据生成最终 Markdown 响应，并填入 response 字段。"),
        ]
    }
    response_obj = await _ainvoke_structured_with_fallback(
        response_prompt,
        llm,
        Response,
        method=method,
        payload=response_payload,
        capability_label="最终响应 structured output",
    )

    if isinstance(response_obj, Response):
        final_response = response_obj.response
    elif isinstance(response_obj, dict):
        final_response = str(response_obj.get("response", ""))
    else:
        raise AIOpsCapabilityError(f"Response structured output 返回未知类型: {type(response_obj)}")

    final_response = assert_non_empty_text(final_response, label="最终响应")
    logger.info(f"最终响应生成完成，长度: {len(final_response)}")

    return {"response": final_response}


async def _ainvoke_structured_with_fallback(
    prompt: ChatPromptTemplate,
    llm: BaseChatModel,
    schema: type[BaseModel],
    *,
    method: str,
    payload: dict[str, Any],
    capability_label: str,
) -> Any:
    """带 asyncio 超时与 json_mode 降级的 structured output 调用。"""
    try:
        return await _ainvoke_structured_once(prompt, llm, schema, method=method, payload=payload)
    except TimeoutError as exc:
        detail = _timeout_detail()
        if method == "function_calling":
            return await _retry_structured_with_json_mode(
                prompt,
                llm,
                schema,
                payload=payload,
                capability_label=capability_label,
                first_detail=detail,
                first_error=exc,
            )
        logger.error("{} {} 失败: {}", capability_label, method, detail, exc_info=True)
        raise _structured_output_error(capability_label, method, detail) from exc
    except Exception as exc:
        detail = format_exception(exc)
        if method == "function_calling" and _is_timeout_like_detail(detail):
            return await _retry_structured_with_json_mode(
                prompt,
                llm,
                schema,
                payload=payload,
                capability_label=capability_label,
                first_detail=detail,
                first_error=exc,
            )
        logger.error("{} {} 失败: {}", capability_label, method, detail, exc_info=True)
        raise _structured_output_error(capability_label, method, detail) from exc


async def _retry_structured_with_json_mode(
    prompt: ChatPromptTemplate,
    llm: BaseChatModel,
    schema: type[BaseModel],
    *,
    payload: dict[str, Any],
    capability_label: str,
    first_detail: str,
    first_error: BaseException,
) -> Any:
    """function_calling 超时后降级 json_mode 再试一次。"""
    logger.warning(
        "{} function_calling 失败，降级到 {} 重试：{}",
        capability_label,
        _JSON_MODE_FALLBACK_METHOD,
        first_detail,
    )
    try:
        return await _ainvoke_structured_once(
            prompt,
            llm,
            schema,
            method=_JSON_MODE_FALLBACK_METHOD,
            payload=payload,
        )
    except TimeoutError as exc:
        detail = f"{_JSON_MODE_FALLBACK_METHOD} {_timeout_detail()}；首错: {first_detail}"
        logger.error("{} {} 失败: {}", capability_label, _JSON_MODE_FALLBACK_METHOD, detail, exc_info=True)
        raise _structured_output_error(capability_label, _JSON_MODE_FALLBACK_METHOD, detail) from exc
    except Exception as exc:
        detail = f"{format_exception(exc)}；首错: {first_detail}"
        logger.error("{} {} 失败: {}", capability_label, _JSON_MODE_FALLBACK_METHOD, detail, exc_info=True)
        raise _structured_output_error(capability_label, _JSON_MODE_FALLBACK_METHOD, detail) from first_error


async def _ainvoke_structured_once(
    prompt: ChatPromptTemplate,
    llm: BaseChatModel,
    schema: type[BaseModel],
    *,
    method: str,
    payload: dict[str, Any],
) -> Any:
    chain = prompt | llm.with_structured_output(schema, method=method)
    timeout_seconds = _llm_timeout_seconds()
    if timeout_seconds <= 0:
        return await chain.ainvoke(payload)
    return await asyncio.wait_for(chain.ainvoke(payload), timeout=timeout_seconds)


def _llm_timeout_seconds() -> float:
    return float(getattr(config, "llm_timeout_seconds", 180.0) or 0)


def _timeout_detail() -> str:
    timeout_seconds = _llm_timeout_seconds()
    return f"asyncio 超时（>{timeout_seconds:g}s）"


def _is_timeout_like_detail(detail: str) -> bool:
    lower_detail = detail.lower()
    return any(marker in lower_detail for marker in _TIMEOUT_DETAIL_MARKERS)


def _structured_output_error(capability_label: str, method: str, detail: str) -> AIOpsCapabilityError:
    return AIOpsCapabilityError(
        f"当前 LLM/中转未能完成 {capability_label}。"
        f"请换支持 {method} 的模型/中转，或调整 AIOPS_STRUCTURED_OUTPUT_METHOD。"
        f"原始错误: {detail}"
    )
