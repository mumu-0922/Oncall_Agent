import importlib
import sys
import types

import pytest
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
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
