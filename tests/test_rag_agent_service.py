import importlib
import sys
import types

import pytest
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES


class FakeChatModel(SimpleChatModel):
    summary_text: str = "压缩后的上下文"

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _call(self, messages, stop=None, run_manager=None, **kwargs) -> str:
        return self.summary_text


def load_rag_agent_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    fake_tools = types.ModuleType("app.tools")

    def retrieve_knowledge(query: str):
        return "ctx", []

    def get_current_time():
        return "now"

    fake_tools.retrieve_knowledge = retrieve_knowledge
    fake_tools.get_current_time = get_current_time
    monkeypatch.setitem(sys.modules, "app.tools", fake_tools)

    fake_mcp_client = types.ModuleType("app.agent.mcp_client")

    async def get_mcp_client_with_retry():
        class DummyClient:
            async def get_tools(self):
                return []

        return DummyClient()

    fake_mcp_client.get_mcp_client_with_retry = get_mcp_client_with_retry
    monkeypatch.setitem(sys.modules, "app.agent.mcp_client", fake_mcp_client)

    sys.modules.pop("app.services.rag_agent_service", None)
    return importlib.import_module("app.services.rag_agent_service")


def test_summary_middleware_summarizes_long_history(monkeypatch: pytest.MonkeyPatch):
    module = load_rag_agent_module(monkeypatch)
    service = module.RagAgentService(
        streaming=False,
        model=FakeChatModel(),
        summary_model=FakeChatModel(),
    )

    middleware = service.middlewares[0]
    state = {
        "messages": [
            HumanMessage(content=f"user-{idx}")
            if idx % 2 == 0
            else AIMessage(content=f"assistant-{idx}")
            for idx in range(12)
        ]
    }

    update = middleware.before_model(state, runtime=None)

    assert update is not None
    assert update["messages"][0].id == REMOVE_ALL_MESSAGES
    summary_message = update["messages"][1]
    assert isinstance(summary_message, HumanMessage)
    assert service._is_summary_message(summary_message)
    assert "压缩后的上下文" in summary_message.content


def test_messages_to_history_skips_system_and_summary_messages(
    monkeypatch: pytest.MonkeyPatch,
):
    module = load_rag_agent_module(monkeypatch)
    service = module.RagAgentService(
        streaming=False,
        model=FakeChatModel(),
        summary_model=FakeChatModel(),
    )

    history = service._messages_to_history(
        [
            SystemMessage(content="system"),
            HumanMessage(
                content="Here is a summary of the conversation to date",
                additional_kwargs={"lc_source": module.SUMMARY_MESSAGE_SOURCE},
            ),
            HumanMessage(content="你好"),
            AIMessage(content="世界"),
        ]
    )

    assert [item["role"] for item in history] == ["user", "assistant"]
    assert [item["content"] for item in history] == ["你好", "世界"]


@pytest.mark.asyncio
async def test_query_only_sends_latest_human_message(monkeypatch: pytest.MonkeyPatch):
    module = load_rag_agent_module(monkeypatch)
    service = module.RagAgentService(
        streaming=False,
        model=FakeChatModel(),
        summary_model=FakeChatModel(),
    )

    class DummyAgent:
        def __init__(self):
            self.last_input = None

        async def ainvoke(self, input, config):
            self.last_input = input
            return {"messages": [AIMessage(content="ok")]}

    dummy_agent = DummyAgent()
    service.agent = dummy_agent

    async def noop():
        return None

    service._initialize_agent = noop

    answer = await service.query("你好", session_id="session-1")

    assert answer == "ok"
    assert len(dummy_agent.last_input["messages"]) == 1
    assert isinstance(dummy_agent.last_input["messages"][0], HumanMessage)


@pytest.mark.asyncio
async def test_query_with_trace_returns_sanitized_tool_events(monkeypatch: pytest.MonkeyPatch):
    module = load_rag_agent_module(monkeypatch)
    service = module.RagAgentService(
        streaming=False,
        model=FakeChatModel(),
        summary_model=FakeChatModel(),
    )

    class DummyAgent:
        async def ainvoke(self, input, config):
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "retrieve_knowledge",
                                "args": {"query": "CPU", "api_key": "sk-secretsecret"},
                                "id": "call-1",
                            }
                        ],
                    ),
                    ToolMessage(
                        content="【参考资料 1】\n来源: runbook.md\n内容:\nCPU 排查",
                        name="retrieve_knowledge",
                        tool_call_id="call-1",
                    ),
                    AIMessage(content="根据 runbook 排查 CPU。"),
                ]
            }

    async def noop():
        return None

    service.agent = DummyAgent()
    service._initialize_agent = noop

    result = await service.query_with_trace("CPU 怎么排查", session_id="session-trace")

    assert result["answer"] == "根据 runbook 排查 CPU。"
    assert [event["kind"] for event in result["trace"]].count("tool_call") == 1
    assert [event["kind"] for event in result["trace"]].count("tool_result") == 1
    tool_call = next(event for event in result["trace"] if event["kind"] == "tool_call")
    assert tool_call["args"]["api_key"] == "***REDACTED***"
    tool_result = next(event for event in result["trace"] if event["kind"] == "tool_result")
    assert tool_result["metadata"]["documents"] == 1
    assert tool_result["metadata"]["sources"] == ["runbook.md"]


@pytest.mark.asyncio
async def test_query_stream_emits_trace_and_content(monkeypatch: pytest.MonkeyPatch):
    module = load_rag_agent_module(monkeypatch)
    service = module.RagAgentService(
        streaming=True,
        model=FakeChatModel(),
        summary_model=FakeChatModel(),
    )

    class DummyAgent:
        async def astream(self, input, config, stream_mode):
            assert stream_mode == ["messages", "updates"]
            yield (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "get_current_time",
                                        "args": {"timezone": "Asia/Shanghai"},
                                        "id": "call-time",
                                    }
                                ],
                            )
                        ]
                    }
                },
            )
            yield (
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="2026-06-23 12:00:00",
                                name="get_current_time",
                                tool_call_id="call-time",
                            )
                        ]
                    }
                },
            )
            yield ("messages", (AIMessage(content="现在是中午。"), {"langgraph_node": "model"}))

    async def noop():
        return None

    service.agent = DummyAgent()
    service._initialize_agent = noop

    chunks = [chunk async for chunk in service.query_stream("现在几点", session_id="session-stream")]

    trace_events = [chunk["data"] for chunk in chunks if chunk["type"] == "trace"]
    assert any(event["kind"] == "tool_call" and event["tool"] == "get_current_time" for event in trace_events)
    assert any(event["kind"] == "tool_result" and event["status"] == "completed" for event in trace_events)
    assert any(chunk["type"] == "content" and chunk["data"] == "现在是中午。" for chunk in chunks)
    done = chunks[-1]
    assert done["type"] == "complete"
    assert done["data"]["answer"] == "现在是中午。"
