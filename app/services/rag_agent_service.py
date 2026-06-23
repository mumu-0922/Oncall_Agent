"""RAG Agent 服务 - 基于 LangGraph 的智能代理。"""

from collections.abc import AsyncGenerator, Sequence
from datetime import datetime
from time import perf_counter
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.checkpoint.memory import MemorySaver
from loguru import logger

from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.core.llm_factory import llm_factory
from app.services.chat_trace_service import ChatTraceObserver
from app.tools import get_current_time, retrieve_knowledge

SUMMARY_MESSAGE_SOURCE = "summarization"
STREAM_MODES = {"messages", "updates"}


class RagAgentService:
    """RAG Agent 服务 - 使用 LangGraph + OpenAI-compatible LLM 工厂。"""

    def __init__(
        self,
        streaming: bool = True,
        model: BaseChatModel | None = None,
        summary_model: BaseChatModel | None = None,
    ):
        """初始化 RAG Agent 服务

        Args:
            streaming: 是否启用流式输出，默认为 True
            model: 可选的主对话模型，用于测试或自定义注入
            summary_model: 可选的摘要模型，用于测试或自定义注入
        """
        self.model_name = config.effective_llm_model
        self.streaming = streaming
        self.system_prompt = self._build_system_prompt()
        self.model = model or self._build_chat_model(streaming=streaming, temperature=0.7)
        self.summary_model = summary_model or self._build_chat_model(streaming=False, temperature=0)
        self.middlewares = self._build_middlewares()

        # 定义基础工具
        self.tools = [retrieve_knowledge, get_current_time]

        # MCP 客户端（延迟初始化，使用全局管理）
        self.mcp_tools: list = []

        # 创建内存检查点（用于会话管理）
        self.checkpointer = MemorySaver()

        # Agent 初始化（会在异步方法中完成）
        self.agent = None
        self._agent_initialized = False

        logger.info(
            f"RAG Agent 服务初始化完成, provider={config.effective_llm_provider}, "
            f"model={self.model_name}, "
            f"streaming={streaming}, middlewares={len(self.middlewares)}"
        )

    def _build_chat_model(self, streaming: bool, temperature: float) -> BaseChatModel:
        """构建 Chat 模型实例。"""
        return llm_factory.create_chat_model(
            model=self.model_name,
            temperature=temperature,
            streaming=streaming,
        )

    def _build_context_summary_prompt(self) -> str:
        """构建对话上下文摘要提示词。"""
        from textwrap import dedent

        return dedent(
            """
            你是对话上下文压缩助手，目标是在不丢失关键事实的前提下，压缩对话历史。

            请基于给定消息历史，提炼后续对话继续所需的最重要上下文，并严格遵守以下要求：
            1. 仅保留与当前会话目标直接相关的信息，不要编造。
            2. 必须保留：用户当前目标、已确认事实、重要约束、关键工具查询结果、尚未解决的问题。
            3. 不要复述无关寒暄或重复内容。
            4. 输出使用简洁中文，按以下结构组织：

            ## 当前目标
            ## 关键上下文
            ## 已确认结论
            ## 待继续事项

            消息历史如下：
            {messages}
            """
        ).strip()

    def _build_middlewares(self) -> list[SummarizationMiddleware]:
        """构建 Agent middleware 列表。"""
        if not config.rag_context_summary_enabled:
            return []

        middleware = SummarizationMiddleware(
            model=self.summary_model,
            trigger=("messages", config.rag_summary_trigger_messages),
            keep=("messages", config.rag_summary_keep_messages),
            trim_tokens_to_summarize=config.rag_summary_trim_tokens,
            summary_prompt=self._build_context_summary_prompt(),
        )
        logger.info(
            "启用对话上下文摘要: trigger={}, keep={}, trim_tokens={}",
            config.rag_summary_trigger_messages,
            config.rag_summary_keep_messages,
            config.rag_summary_trim_tokens,
        )
        return [middleware]

    async def _initialize_agent(self):
        """异步初始化 Agent（包括 MCP 工具）"""
        if self._agent_initialized:
            return

        self.mcp_tools = await self._load_mcp_tools()

        # 合并所有工具
        all_tools = self.tools + self.mcp_tools

        self.agent = create_agent(
            self.model,
            tools=all_tools,
            system_prompt=self.system_prompt,
            middleware=self.middlewares,
            checkpointer=self.checkpointer,
        )

        self._agent_initialized = True


        if all_tools:
            tool_names = [tool.name if hasattr(tool, "name") else str(tool) for tool in all_tools]
            logger.info(f"可用工具列表: {', '.join(tool_names)}")

    async def _load_mcp_tools(self) -> list[Any]:
        """加载 MCP 工具；MCP 不可用时降级为仅使用本地工具。"""
        try:
            mcp_client = await get_mcp_client_with_retry()
            mcp_tools = await mcp_client.get_tools()
            logger.info(f"成功加载 {len(mcp_tools)} 个 MCP 工具")
            return mcp_tools
        except Exception as e:
            logger.warning(
                "加载 MCP 工具失败，普通对话将降级为本地工具模式: {}",
                self._format_exception(e),
            )
            return []

    def _format_exception(self, error: BaseException) -> str:
        """展开 ExceptionGroup，避免日志只显示 TaskGroup 外壳。"""
        if isinstance(error, BaseExceptionGroup):
            sub_errors = "; ".join(self._format_exception(exc) for exc in error.exceptions)
            return f"{type(error).__name__}: {error} -> [{sub_errors}]"
        return f"{type(error).__name__}: {error}"

    def _build_system_prompt(self) -> str:
        """
        构建系统提示词

        注意：LangChain 框架会自动将工具信息传递给 LLM，
        因此系统提示词中无需列举具体的工具列表。

        Returns:
            str: 系统提示词
        """
        from textwrap import dedent

        return dedent("""
            你是一个专业的AI助手，能够使用多种工具来帮助用户解决问题。

            工作原则:
            1. 理解用户需求，选择合适的工具来完成任务
            2. 当需要获取实时信息或专业知识时，主动使用相关工具
            3. 基于工具返回的结果提供准确、专业的回答
            4. 如果工具无法提供足够信息，请诚实地告知用户

            回答要求:
            - 保持友好、专业的语气
            - 回答简洁明了，重点突出
            - 基于事实，不编造信息
            - 如有不确定的地方，明确说明

            请根据用户的问题，灵活使用可用工具，提供高质量的帮助。
        """).strip()

    async def query(
        self,
        question: str,
        session_id: str,
    ) -> str:
        """
        非流式处理用户问题（一次性返回完整答案）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）

        Returns:
            str: 完整答案
        """
        result = await self.query_with_trace(question, session_id=session_id)
        return result["answer"]

    async def query_with_trace(
        self,
        question: str,
        session_id: str,
    ) -> dict[str, Any]:
        """非流式处理用户问题，并返回可审计执行轨迹。"""
        try:
            await self._initialize_agent()

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（非流式）: {question}")
            started_at = perf_counter()
            observer = ChatTraceObserver()
            trace_events = [observer.start_event()]

            result = await self.agent.ainvoke(
                input=self._build_agent_input(question),
                config=self._build_thread_config(session_id),
            )

            messages_result = result.get("messages", [])
            trace_events.extend(observer.extract_trace_from_messages(messages_result))
            if not messages_result:
                logger.warning(f"[会话 {session_id}] Agent 返回结果为空")
                trace_events.append(self._empty_trace_event(observer, started_at))
                return {"answer": "", "trace": trace_events}

            answer = self._extract_final_answer(messages_result[-1])
            self._log_final_tool_calls(messages_result[-1], session_id=session_id)
            trace_events.append(self._complete_trace_event(observer, started_at))
            logger.info(f"[会话 {session_id}] RAG Agent 查询完成（非流式）")
            return {"answer": answer, "trace": trace_events}

        except Exception as e:
            logger.error(
                "[会话 {}] RAG Agent 查询失败（非流式）: {}",
                session_id,
                self._format_exception(e),
            )
            raise

    async def query_stream(
        self,
        question: str,
        session_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        流式处理用户问题（逐步返回答案片段）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）

        Yields:
            Dict[str, Any]: 包含流式数据的字典
                - type: "content" | "tool_call" | "complete" | "error"
                - data: 具体内容
        """
        try:
            await self._initialize_agent()

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（流式）: {question}")
            started_at = perf_counter()
            full_response = ""
            trace_events: list[dict[str, Any]] = []
            observer = ChatTraceObserver()

            start_event = observer.start_event()
            trace_events.append(start_event)
            yield {"type": "trace", "data": start_event}

            async for stream_item in self.agent.astream(
                input=self._build_agent_input(question),
                config=self._build_thread_config(session_id),
                stream_mode=["messages", "updates"],
            ):
                stream_mode, payload = self._normalize_stream_item(stream_item)
                if stream_mode != "messages":
                    async for event_chunk in self._stream_update_trace(
                        payload, observer, trace_events
                    ):
                        yield event_chunk
                    continue

                token, metadata = self._split_message_payload(payload)
                async for chunk in self._stream_message_trace_and_content(
                    token, metadata, observer, trace_events
                ):
                    if chunk["type"] == "content":
                        full_response += chunk["data"]
                    yield chunk

            logger.info(f"[会话 {session_id}] RAG Agent 查询完成（流式）")
            async for chunk in self._stream_complete_chunks(
                full_response, observer, trace_events, started_at
            ):
                yield chunk

        except Exception as e:
            logger.error(
                "[会话 {}] RAG Agent 查询失败（流式）: {}",
                session_id,
                self._format_exception(e),
            )
            yield self._error_chunk(e)
            raise

    def _build_agent_input(self, question: str) -> dict[str, list[HumanMessage]]:
        """构建 Agent 输入；系统提示词由 create_agent 接管。"""
        return {"messages": [HumanMessage(content=question)]}

    def _build_thread_config(self, session_id: str) -> dict[str, dict[str, str]]:
        """配置 thread_id，用于 LangGraph 会话持久化。"""
        return {"configurable": {"thread_id": session_id}}

    def _extract_final_answer(self, message: BaseMessage) -> str:
        content = message.content if hasattr(message, "content") else str(message)
        return content if isinstance(content, str) else str(content)

    def _log_final_tool_calls(self, message: BaseMessage, *, session_id: str) -> None:
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            return
        tool_names = [tc.get("name", "unknown") for tc in tool_calls]
        logger.info(f"[会话 {session_id}] Agent 调用了工具: {tool_names}")

    def _complete_trace_event(
        self, observer: ChatTraceObserver, started_at: float
    ) -> dict[str, Any]:
        return observer.complete_event(round((perf_counter() - started_at) * 1000))

    def _empty_trace_event(self, observer: ChatTraceObserver, started_at: float) -> dict[str, Any]:
        return observer.empty_result_event(round((perf_counter() - started_at) * 1000))

    def _normalize_stream_item(self, stream_item: Any) -> tuple[str, Any]:
        if (
            isinstance(stream_item, tuple)
            and len(stream_item) == 2
            and stream_item[0] in STREAM_MODES
        ):
            return stream_item
        return "messages", stream_item

    def _split_message_payload(self, payload: Any) -> tuple[Any, dict[str, Any]]:
        if isinstance(payload, tuple) and len(payload) == 2:
            token, metadata = payload
            return token, metadata if isinstance(metadata, dict) else {}
        return payload, {}

    async def _stream_update_trace(
        self,
        payload: Any,
        observer: ChatTraceObserver,
        trace_events: list[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        for event in observer.events_from_update(payload):
            trace_events.append(event)
            yield {"type": "trace", "data": event}

    async def _stream_message_trace_and_content(
        self,
        token: Any,
        metadata: dict[str, Any],
        observer: ChatTraceObserver,
        trace_events: list[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        node_name = metadata.get("langgraph_node", "unknown")
        for event in observer.events_from_message(token, node_name=node_name):
            trace_events.append(event)
            yield {"type": "trace", "data": event}

        text_content = observer.extract_ai_text(token)
        if text_content:
            yield {"type": "content", "data": text_content, "node": node_name}

    async def _stream_complete_chunks(
        self,
        full_response: str,
        observer: ChatTraceObserver,
        trace_events: list[dict[str, Any]],
        started_at: float,
    ) -> AsyncGenerator[dict[str, Any], None]:
        complete_event = self._complete_trace_event(observer, started_at)
        trace_events.append(complete_event)
        yield {"type": "trace", "data": complete_event}
        yield {
            "type": "complete",
            "data": {
                "answer": full_response,
                "trace": trace_events,
                "duration_ms": complete_event.get("duration_ms"),
            },
        }

    def _error_chunk(self, error: BaseException) -> dict[str, str]:
        return {"type": "error", "data": self._format_exception(error)}

    @staticmethod
    def _is_summary_message(message: BaseMessage) -> bool:
        """判断消息是否为上下文摘要消息。"""
        additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
        return additional_kwargs.get("lc_source") == SUMMARY_MESSAGE_SOURCE

    def _messages_to_history(self, messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
        """将内部消息列表转换为前端展示历史。"""
        history: list[dict[str, str]] = []

        for msg in messages:
            # 隐藏系统提示词和内部摘要消息，避免污染前端对话记录
            if isinstance(msg, SystemMessage) or self._is_summary_message(msg):
                continue

            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            content = msg.content if hasattr(msg, "content") else str(msg)
            timestamp = getattr(msg, "timestamp", None) or datetime.now().isoformat()

            history.append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": timestamp,
                }
            )

        return history

    def get_session_history(self, session_id: str) -> list:
        """
        获取会话历史（从 MemorySaver checkpointer 中读取）

        Args:
            session_id: 会话ID（即 thread_id）

        Returns:
            list: 消息历史列表 [{"role": "user|assistant", "content": "...", "timestamp": "..."}]
        """
        try:
            # 使用 checkpointer 的 get 方法获取最新的检查点
            config = {"configurable": {"thread_id": session_id}}

            # 获取该 thread 的最新检查点
            checkpoint_tuple = self.checkpointer.get(config)

            if not checkpoint_tuple:
                logger.info(f"获取会话历史: {session_id}, 消息数量: 0")
                return []

            # checkpoint_tuple 可能是命名元组或普通元组，安全地提取 checkpoint
            # 通常第一个元素是 checkpoint 数据
            if hasattr(checkpoint_tuple, 'checkpoint'):
                checkpoint_data = checkpoint_tuple.checkpoint  # type: ignore
            else:
                # 如果是普通元组，第一个元素是 checkpoint
                checkpoint_data = checkpoint_tuple[0] if checkpoint_tuple else {}

            # 从检查点中提取消息
            messages = checkpoint_data.get("channel_values", {}).get("messages", [])
            history = self._messages_to_history(messages)

            logger.info(f"获取会话历史: {session_id}, 消息数量: {len(history)}")
            return history

        except Exception as e:
            logger.error(f"获取会话历史失败: {session_id}, 错误: {e}")
            return []

    def clear_session(self, session_id: str) -> bool:
        """
        清空会话历史（从 MemorySaver checkpointer 中删除）

        Args:
            session_id: 会话ID（即 thread_id）

        Returns:
            bool: 是否成功
        """
        try:
            # 使用 checkpointer 的 delete_thread 方法删除该 thread 的所有检查点
            self.checkpointer.delete_thread(session_id)

            logger.info(f"已清除会话历史: {session_id}")
            return True

        except Exception as e:
            logger.error(f"清空会话历史失败: {session_id}, 错误: {e}")
            return False

    async def cleanup(self):
        """清理资源"""
        try:
            logger.info("清理 RAG Agent 服务资源...")
            # MCP 客户端由全局管理器统一管理，无需手动清理
            logger.info("RAG Agent 服务资源已清理")
        except Exception as e:
            logger.error(f"清理资源失败: {e}")


# 全局单例 - 启用流式输出
rag_agent_service = RagAgentService(streaming=config.llm_streaming)
