import importlib

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.agent.aiops.utils import (
    AIOpsCapabilityError,
    AIOpsDependencyError,
    load_aiops_tools_strict,
)

executor_module = importlib.import_module("app.agent.aiops.executor")
planner_module = importlib.import_module("app.agent.aiops.planner")
replanner_module = importlib.import_module("app.agent.aiops.replanner")


@tool
async def dummy_mcp_tool(text: str = "ok") -> str:
    """dummy mcp tool"""
    return text


@tool
async def query_cpu_metrics(service_name: str = "app") -> str:
    """query cpu metrics"""
    return f"cpu ok for {service_name}"


@tool
async def query_memory_metrics(service_name: str = "app") -> str:
    """query memory metrics"""
    return f"memory ok for {service_name}"


@tool
async def retrieve_knowledge(query: str = "q") -> str:
    """retrieve knowledge"""
    return f"knowledge for {query}"


class DummyClient:
    def __init__(self, tools):
        self._tools = tools

    async def get_tools(self):
        return self._tools


@pytest.mark.asyncio
async def test_load_aiops_tools_strict_rejects_empty_mcp_tools(monkeypatch):
    async def fake_get_client(*args, **kwargs):
        return DummyClient([])

    monkeypatch.setattr("app.agent.aiops.utils.get_mcp_client_with_retry", fake_get_client)

    with pytest.raises(AIOpsDependencyError, match="工具列表为空"):
        await load_aiops_tools_strict()


@pytest.mark.asyncio
async def test_load_aiops_tools_strict_returns_local_and_mcp(monkeypatch):
    mcp_tool = dummy_mcp_tool

    async def fake_get_client(*args, **kwargs):
        return DummyClient([mcp_tool])

    monkeypatch.setattr("app.agent.aiops.utils.get_mcp_client_with_retry", fake_get_client)

    local_tools, mcp_tools = await load_aiops_tools_strict()

    assert len(local_tools) >= 2
    assert mcp_tools == [mcp_tool]


@pytest.mark.asyncio
async def test_executor_rejects_model_without_tool_calls(monkeypatch):
    class NoToolCallModel:
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="我不调用工具，直接回答")

    async def fake_load_tools():
        return [dummy_mcp_tool], [dummy_mcp_tool]

    monkeypatch.setattr(executor_module, "load_aiops_tools_strict", fake_load_tools)
    monkeypatch.setattr(executor_module.llm_factory, "create_chat_model", lambda **kwargs: NoToolCallModel())
    monkeypatch.setattr(executor_module.config, "aiops_require_tool_call", True)

    with pytest.raises(AIOpsCapabilityError, match="没有产生任何 tool_calls"):
        await executor_module.executor(
            {
                "input": "诊断 CPU 告警",
                "plan": ["查询 data-sync-service CPU 指标"],
                "past_steps": [],
                "response": "",
            }
        )


@pytest.mark.asyncio
async def test_planner_uses_structured_output_without_default_fallback(monkeypatch):
    class DummyPrompt:
        def __or__(self, other):
            return other

    class StructuredChain:
        def __init__(self, schema, steps=None, error=None):
            self.schema = schema
            self.steps = steps or []
            self.error = error

        def __ror__(self, other):
            return self

        async def ainvoke(self, _):
            if self.error:
                raise self.error
            return self.schema(steps=self.steps)

    class StructuredModel:
        def with_structured_output(self, schema, method):
            return StructuredChain(schema, steps=["调用 query_cpu_metrics 获取 CPU 指标"])

    class DummyKnowledgeTool:
        async def ainvoke(self, args):
            return ""

    async def fake_load_tools():
        return [dummy_mcp_tool], [dummy_mcp_tool]

    monkeypatch.setattr(planner_module, "retrieve_knowledge", DummyKnowledgeTool())
    monkeypatch.setattr(planner_module, "planner_prompt", DummyPrompt())
    monkeypatch.setattr(planner_module, "load_aiops_tools_strict", fake_load_tools)
    monkeypatch.setattr(planner_module.llm_factory, "create_chat_model", lambda **kwargs: StructuredModel())

    result = await planner_module.planner(
        {"input": "诊断 CPU 告警", "plan": [], "past_steps": [], "response": ""}
    )

    assert result["plan"] == ["调用 query_cpu_metrics 获取 CPU 指标"]


@pytest.mark.asyncio
async def test_planner_propagates_structured_output_failure(monkeypatch):
    class DummyPrompt:
        def __or__(self, other):
            return other

    class BrokenStructuredChain:
        def __ror__(self, other):
            return self

        async def ainvoke(self, _):
            raise ValueError("structured output unsupported")

    class BrokenStructuredModel:
        def with_structured_output(self, schema, method):
            return BrokenStructuredChain()

    class DummyKnowledgeTool:
        async def ainvoke(self, args):
            return ""

    async def fake_load_tools():
        return [dummy_mcp_tool], [dummy_mcp_tool]

    monkeypatch.setattr(planner_module, "retrieve_knowledge", DummyKnowledgeTool())
    monkeypatch.setattr(planner_module, "planner_prompt", DummyPrompt())
    monkeypatch.setattr(planner_module, "load_aiops_tools_strict", fake_load_tools)
    monkeypatch.setattr(
        planner_module.llm_factory,
        "create_chat_model",
        lambda **kwargs: BrokenStructuredModel(),
    )

    with pytest.raises(AIOpsCapabilityError, match="Planner structured output"):
        await planner_module.planner(
            {"input": "诊断 CPU 告警", "plan": [], "past_steps": [], "response": ""}
        )


@pytest.mark.asyncio
async def test_executor_returns_tool_trace_events(monkeypatch):
    class ToolCallingModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "dummy_mcp_tool",
                            "args": {"text": "cpu ok", "api_key": "sk-secret-value"},
                            "id": "call_1",
                        }
                    ],
                )
            return AIMessage(content="基于 dummy_mcp_tool 返回，CPU 指标正常")

    async def fake_load_tools():
        return [], [dummy_mcp_tool]

    monkeypatch.setattr(executor_module, "load_aiops_tools_strict", fake_load_tools)
    monkeypatch.setattr(
        executor_module.llm_factory,
        "create_chat_model",
        lambda **kwargs: ToolCallingModel(),
    )
    monkeypatch.setattr(executor_module.config, "aiops_require_tool_call", True)

    result = await executor_module.executor(
        {
            "input": "诊断 CPU 告警",
            "plan": ["调用 dummy_mcp_tool 查询 CPU 指标"],
            "past_steps": [],
            "tool_events": [],
            "response": "",
        }
    )

    trace_events = result["tool_events"]
    assert result["plan"] == []
    assert result["past_steps"]
    assert any(
        event["kind"] == "tool_call" and event["tool"] == "dummy_mcp_tool"
        for event in trace_events
    )
    assert any(
        event["kind"] == "tool_result" and "cpu ok" in event["summary"]
        for event in trace_events
    )
    assert "工具证据" in result["past_steps"][0][1]
    assert "cpu ok" in result["past_steps"][0][1]
    tool_call = next(event for event in trace_events if event["kind"] == "tool_call")
    assert tool_call["args"]["api_key"] == "***REDACTED***"


def test_aiops_service_formats_tool_trace_events():
    from app.services.aiops_service import AIOpsService

    service = object.__new__(AIOpsService)
    events = service._format_tool_trace_events(
        {
            "tool_events": [
                {
                    "kind": "tool_call",
                    "title": "调用工具 dummy_mcp_tool",
                    "status": "started",
                    "tool": "dummy_mcp_tool",
                }
            ]
        }
    )

    assert events == [
        {
            "type": "trace",
            "stage": "trace",
            "data": {
                "kind": "tool_call",
                "title": "调用工具 dummy_mcp_tool",
                "status": "started",
                "tool": "dummy_mcp_tool",
            },
        }
    ]


@pytest.mark.asyncio
async def test_executor_requires_tool_choice_when_configured(monkeypatch):
    class ToolChoiceModel:
        def __init__(self):
            self.bound_tool_choices = []
            self.calls = 0

        def bind_tools(self, tools, **kwargs):
            self.bound_tool_choices.append(kwargs.get("tool_choice"))
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "dummy_mcp_tool",
                            "args": {"text": "ok"},
                            "id": "call_required",
                        }
                    ],
                )
            return AIMessage(content="工具已调用")

    model = ToolChoiceModel()

    async def fake_load_tools():
        return [], [dummy_mcp_tool]

    monkeypatch.setattr(executor_module, "load_aiops_tools_strict", fake_load_tools)
    monkeypatch.setattr(executor_module.llm_factory, "create_chat_model", lambda **kwargs: model)
    monkeypatch.setattr(executor_module.config, "aiops_require_tool_call", True)

    await executor_module.executor(
        {
            "input": "诊断 CPU 告警",
            "plan": ["调用 dummy_mcp_tool 查询 CPU 指标"],
            "past_steps": [],
            "tool_events": [],
            "response": "",
        }
    )

    assert model.bound_tool_choices == ["required", "auto"]


@pytest.mark.asyncio
async def test_executor_skips_second_llm_call_by_default(monkeypatch):
    class SingleToolCallModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("Executor 默认不应发起第二轮 LLM 总结")
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "dummy_mcp_tool",
                        "args": {"text": "timestamp ok"},
                        "id": "call_no_second_llm",
                    }
                ],
            )

    model = SingleToolCallModel()

    async def fake_load_tools():
        return [], [dummy_mcp_tool]

    monkeypatch.setattr(executor_module, "load_aiops_tools_strict", fake_load_tools)
    monkeypatch.setattr(
        executor_module.llm_factory,
        "create_chat_model",
        lambda **kwargs: model,
    )
    monkeypatch.setattr(executor_module.config, "aiops_require_tool_call", True)
    monkeypatch.setattr(executor_module.config, "aiops_executor_summarize_with_llm", False)

    result = await executor_module.executor(
        {
            "input": "诊断 CPU 告警",
            "plan": ["调用 dummy_mcp_tool 查询当前时间"],
            "past_steps": [],
            "tool_events": [],
            "response": "",
        }
    )

    assert model.calls == 1
    assert result["plan"] == []
    assert "timestamp ok" in result["past_steps"][0][1]


@pytest.mark.asyncio
async def test_executor_limits_binding_to_explicit_step_tools(monkeypatch):
    class CaptureBindingModel:
        def __init__(self):
            self.bound_tool_names = []
            self.calls = 0

        def bind_tools(self, tools, **kwargs):
            self.bound_tool_names.append([tool.name for tool in tools])
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_cpu_metrics",
                        "args": {"service_name": "super-biz-agent"},
                        "id": "call_cpu",
                    },
                    {
                        "name": "query_memory_metrics",
                        "args": {"service_name": "super-biz-agent"},
                        "id": "call_mem",
                    },
                ],
            )

    model = CaptureBindingModel()

    async def fake_load_tools():
        return [retrieve_knowledge], [query_cpu_metrics, query_memory_metrics, dummy_mcp_tool]

    monkeypatch.setattr(executor_module, "load_aiops_tools_strict", fake_load_tools)
    monkeypatch.setattr(
        executor_module.llm_factory,
        "create_chat_model",
        lambda **kwargs: model,
    )
    monkeypatch.setattr(executor_module.config, "aiops_require_tool_call", True)
    monkeypatch.setattr(executor_module.config, "aiops_executor_summarize_with_llm", False)

    result = await executor_module.executor(
        {
            "input": "诊断 CPU 和内存告警",
            "plan": [
                "使用 query_cpu_metrics 和 query_memory_metrics 查询 super-biz-agent 指标"
            ],
            "past_steps": [],
            "tool_events": [],
            "response": "",
        }
    )

    assert model.bound_tool_names == [
        ["query_cpu_metrics", "query_memory_metrics"],
        ["query_cpu_metrics", "query_memory_metrics"],
    ]
    assert "query_cpu_metrics" in result["past_steps"][0][1]
    assert "query_memory_metrics" in result["past_steps"][0][1]
    assert "retrieve_knowledge" not in result["past_steps"][0][1]


@pytest.mark.asyncio
async def test_executor_rejects_wrong_tool_when_step_names_monitor_tool(monkeypatch):
    class WrongToolModel:
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve_knowledge",
                        "args": {"query": "cpu经验"},
                        "id": "call_wrong",
                    }
                ],
            )

    async def fake_load_tools():
        return [retrieve_knowledge], [query_cpu_metrics]

    monkeypatch.setattr(executor_module, "load_aiops_tools_strict", fake_load_tools)
    monkeypatch.setattr(
        executor_module.llm_factory,
        "create_chat_model",
        lambda **kwargs: WrongToolModel(),
    )
    monkeypatch.setattr(executor_module.config, "aiops_require_tool_call", True)

    with pytest.raises(AIOpsCapabilityError, match="未指定的工具: retrieve_knowledge"):
        await executor_module.executor(
            {
                "input": "诊断 CPU 告警",
                "plan": ["必须使用 query_cpu_metrics 查询 CPU 指标"],
                "past_steps": [],
                "tool_events": [],
                "response": "",
            }
        )


@pytest.mark.asyncio
async def test_executor_continues_until_all_explicit_tools_are_called(monkeypatch):
    class PartialThenCompleteModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "query_cpu_metrics",
                            "args": {"service_name": "super-biz-agent"},
                            "id": "call_cpu",
                        }
                    ],
                )
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_memory_metrics",
                        "args": {"service_name": "super-biz-agent"},
                        "id": "call_mem",
                    }
                ],
            )

    model = PartialThenCompleteModel()

    async def fake_load_tools():
        return [], [query_cpu_metrics, query_memory_metrics]

    monkeypatch.setattr(executor_module, "load_aiops_tools_strict", fake_load_tools)
    monkeypatch.setattr(
        executor_module.llm_factory,
        "create_chat_model",
        lambda **kwargs: model,
    )
    monkeypatch.setattr(executor_module.config, "aiops_require_tool_call", True)
    monkeypatch.setattr(executor_module.config, "aiops_executor_summarize_with_llm", False)
    monkeypatch.setattr(executor_module.config, "aiops_tool_call_max_rounds", 3)

    result = await executor_module.executor(
        {
            "input": "诊断 CPU 和内存告警",
            "plan": ["使用 query_cpu_metrics 和 query_memory_metrics 查询指标"],
            "past_steps": [],
            "tool_events": [],
            "response": "",
        }
    )

    assert model.calls == 2
    assert "cpu ok for super-biz-agent" in result["past_steps"][0][1]
    assert "memory ok for super-biz-agent" in result["past_steps"][0][1]


def test_extract_explicit_tool_names_ignores_negated_knowledge_tool():
    names = executor_module._extract_explicit_tool_names(
        "使用 query_cpu_metrics 查询 CPU，禁止用 retrieve_knowledge 替代真实证据",
        ["query_cpu_metrics", "retrieve_knowledge"],
    )

    assert names == ["query_cpu_metrics"]


@pytest.mark.asyncio
async def test_replanner_response_truncates_long_step_results(monkeypatch):
    class DummyPrompt:
        def __or__(self, other):
            return other

    class CaptureChain:
        def __init__(self, schema):
            self.schema = schema
            self.payload = None

        async def ainvoke(self, payload):
            self.payload = payload
            return self.schema(response="ok")

    class CaptureModel:
        def __init__(self):
            self.chain = None

        def with_structured_output(self, schema, method):
            self.chain = CaptureChain(schema)
            return self.chain

    model = CaptureModel()
    metric_json = (
        '{"service_name":"super-biz-agent","metric_name":"cpu_usage_percent",'
        '"source":"local_wsl:/proc","history_available":false,'
        '"data_points":[{"timestamp":"11:00:00","value":12.3}],'
        '"alert_info":{"triggered":false}}'
    )
    long_result = metric_json + "\n" + "A" * (replanner_module._RESPONSE_STEP_RESULT_MAX_CHARS + 500)

    monkeypatch.setattr(replanner_module, "response_prompt", DummyPrompt())
    monkeypatch.setattr(replanner_module.config, "aiops_structured_output_method", "function_calling")
    monkeypatch.setattr(replanner_module.config, "llm_timeout_seconds", 1)

    result = await replanner_module._generate_response(
        {
            "input": "诊断告警",
            "plan": [],
            "past_steps": [("查询日志", long_result)],
            "tool_events": [],
            "response": "",
        },
        model,
    )

    history_message = model.chain.payload["messages"][1][1]
    assert result["response"].startswith("ok")
    assert result["evidence_package"]["actionable_evidence_count"] == 1
    assert "E001-metric" in result["response"]
    assert "结果过长已截断" in history_message
    assert f"原始长度 {len(long_result)} 字符" in history_message
    assert len(history_message) < len(long_result)


@pytest.mark.asyncio
async def test_replanner_structured_output_falls_back_to_json_mode_on_timeout(monkeypatch):
    class DummyPrompt:
        def __or__(self, other):
            return other

    class MethodChain:
        def __init__(self, schema, method):
            self.schema = schema
            self.method = method

        async def ainvoke(self, payload):
            if self.method == "function_calling":
                raise TimeoutError("upstream timed out")
            return self.schema(response="json mode ok")

    class FallbackModel:
        def __init__(self):
            self.methods = []

        def with_structured_output(self, schema, method):
            self.methods.append(method)
            return MethodChain(schema, method)

    model = FallbackModel()

    monkeypatch.setattr(replanner_module.config, "llm_timeout_seconds", 1)

    response = await replanner_module._ainvoke_structured_with_fallback(
        DummyPrompt(),
        model,
        replanner_module.Response,
        method="function_calling",
        payload={"messages": []},
        capability_label="最终响应 structured output",
    )

    assert model.methods == ["function_calling", "json_mode"]
    assert response.response == "json mode ok"
