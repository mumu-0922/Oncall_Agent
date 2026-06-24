"""
通用 Plan-Execute-Replan 状态定义
基于 LangGraph 官方教程实现
"""

import operator
from typing import Annotated, TypedDict


class PlanExecuteState(TypedDict):
    """Plan-Execute-Replan 状态"""

    # 用户输入（任务描述）
    input: str

    # 执行计划（步骤列表）
    plan: list[str]

    # 已执行的步骤历史
    # 使用 operator.add 实现追加式更新（而非覆盖）
    past_steps: Annotated[list[tuple], operator.add]

    # 可审计执行轨迹：阶段摘要、工具调用、工具结果。
    # 使用 operator.add 追加每个节点本次产出的事件；不包含模型隐藏思维链。
    tool_events: Annotated[list[dict], operator.add]

    # 最终报告生成前沉淀的结构化证据包。
    # 报告必须基于该证据包；没有可用证据时必须明确“证据不足”。
    evidence_package: dict

    # 最终响应/报告
    response: str
