"""
Planner 节点：制定执行计划
基于 LangGraph 官方教程实现
"""

from textwrap import dedent
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config
from app.core.llm_factory import llm_factory
from app.tools import retrieve_knowledge

from .state import PlanExecuteState
from .utils import (
    AIOpsCapabilityError,
    AIOpsExecutionError,
    format_exception,
    format_tools_description,
    load_aiops_tools_strict,
)


class Plan(BaseModel):
    """计划的输出格式。"""

    steps: list[str] = Field(
        description="完成任务所需的不同步骤。这些步骤应该按顺序执行，每一步都建立在前一步的基础上。"
    )


# Planner 提示词
planner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent("""
                作为一个专家级别的 AIOps 规划者，你需要将复杂的运维诊断任务分解为可执行步骤。

                可用工具列表（用于制定计划时参考）：

                {tools_description}

                注意：你的职责是制定计划，实际工具调用由 Executor 负责执行。

                {experience_context}

                对于给定任务，请创建简单、逐步、证据驱动的计划。计划必须满足：
                - 每个步骤都要明确要调用哪些工具，以及工具所需的关键参数。
                - 必须优先收集真实监控/日志证据，不允许直接编结论。
                - 步骤之间应该有清晰依赖关系。
                - 步骤描述要具体、可操作。
                - 如果有相关经验文档，请参考其中的方法和步骤制定计划。
                - 最后一步不要写“生成报告”，最终报告由 Replanner 统一生成；计划只包含证据收集和分析步骤。

                示例输入："分析当前默认/已配置核心服务是否存在 CPU 告警"
                示例计划：
                1. 使用 query_cpu_metrics 工具查询默认/已配置核心服务 CPU 指标，确认是否超过阈值；若工具返回 error，记录原始错误并停止伪推断
                2. 使用 search_topic_by_service_name 工具查找同一服务对应日志 topic；若未找到，记录原始错误并说明日志证据缺失
                3. 使用 get_current_timestamp 与 search_log 工具查询最近 15 分钟 ERROR 日志，寻找与 CPU 异常相关的证据
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


async def planner(state: PlanExecuteState) -> dict[str, Any]:
    """
    规划节点：根据用户输入生成执行计划。

    流程：
    1. 查询内部文档，获取相关经验和最佳实践。
    2. 严格加载 MCP 工具；失败直接中断，不生成假计划。
    3. 使用模型 structured output 生成 Plan；失败直接暴露能力缺口。
    """
    logger.info("=== Planner：制定执行计划 ===")

    input_text = state.get("input", "")
    logger.info(f"用户输入: {input_text}")

    # 步骤1: 查询内部文档获取相关经验。知识库不是 AIOps 必需依赖，失败只记录。
    logger.info("查询内部文档，寻找相关经验...")
    experience_docs = ""
    try:
        # retrieve_knowledge 使用 response_format="content_and_artifact"；ainvoke() 返回 content。
        context_str = await retrieve_knowledge.ainvoke({"query": input_text})
        if context_str and context_str.strip():
            experience_docs = context_str
            logger.info(f"找到相关经验文档，长度: {len(experience_docs)}")
        else:
            logger.info("未找到相关经验文档")
    except Exception as exc:
        logger.warning("查询内部文档失败，不影响 MCP 诊断链路: {}", format_exception(exc))

    # 步骤2: 严格获取可用工具列表。MCP 是 AIOps 必需依赖。
    local_tools, mcp_tools = await load_aiops_tools_strict()
    tools_description = format_tools_description(local_tools + mcp_tools)

    # 步骤3: 格式化经验文档上下文。
    if experience_docs:
        experience_context = dedent(f"""
            ## 相关经验文档

            以下是从知识库中检索到的相关经验和最佳实践，请参考这些经验制定计划：

            {experience_docs}

            ---
        """).strip()
    else:
        experience_context = ""

    # 步骤4: 创建 LLM 并生成结构化计划。
    llm = llm_factory.create_chat_model(
        model=config.effective_llm_model,
        temperature=0,
        streaming=False,
    )
    method = _structured_method()
    logger.info("Planner 使用 structured_output method={}", method)

    try:
        planner_chain = planner_prompt | llm.with_structured_output(Plan, method=method)
        plan_result = await planner_chain.ainvoke(
            {
                "messages": [
                    (
                        "user",
                        input_text
                        + "\n\n必须返回 Plan 结构：steps 为 2-5 个证据收集/分析步骤。",
                    )
                ],
                "tools_description": tools_description,
                "experience_context": experience_context,
            }
        )
    except Exception as exc:
        detail = format_exception(exc)
        logger.error("生成结构化计划失败: {}", detail, exc_info=True)
        raise AIOpsCapabilityError(
            "当前 LLM/中转未能完成 AIOps Planner structured output。"
            f"请换支持 {method} 的模型/中转，或调整 AIOPS_STRUCTURED_OUTPUT_METHOD。"
            f"原始错误: {detail}"
        ) from exc

    if isinstance(plan_result, Plan):
        plan_steps = plan_result.steps
    elif isinstance(plan_result, dict):
        raw_steps = plan_result.get("steps", [])
        plan_steps = raw_steps if isinstance(raw_steps, list) else []
    else:
        raise AIOpsCapabilityError(f"Planner structured output 返回了未知类型: {type(plan_result)}")

    plan_steps = [str(step).strip() for step in plan_steps if str(step).strip()]
    if not plan_steps:
        raise AIOpsExecutionError("Planner 生成了空计划，拒绝继续执行伪诊断")

    logger.info(f"计划已生成，共 {len(plan_steps)} 个步骤")
    for i, step in enumerate(plan_steps, 1):
        logger.info(f"  步骤{i}: {step}")

    return {"plan": plan_steps}
