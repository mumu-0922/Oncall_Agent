"""RAG Agent 服务 - 基于 LangGraph 的智能代理

使用 langchain_qwq 的 ChatQwen 原生集成，
支持真正的流式输出和更好的模型适配。
"""

from collections.abc import AsyncGenerator, Sequence
from datetime import datetime
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_qwq import ChatQwen
from langgraph.checkpoint.memory import MemorySaver
from loguru import logger

from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.tools import get_current_time, retrieve_knowledge

# 阿里千问大模型和langchain集成参考： https://docs.langchain.com/oss/python/integrations/chat/qwen
# 注意：需要配置环境变量 DASHSCOPE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1 否则默认访问的是新加坡站点
# 同时也需要配置环境变量 DASHSCOPE_API_KEY=your_api_key

SUMMARY_MESSAGE_SOURCE = "summarization"


class RagAgentService:
    """RAG Agent 服务 - 使用 LangGraph + ChatQwen 原生集成"""

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
        self.model_name = config.rag_model
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
            f"RAG Agent 服务初始化完成 (ChatQwen), model={self.model_name}, "
            f"streaming={streaming}, middlewares={len(self.middlewares)}"
        )

    def _build_chat_model(self, streaming: bool, temperature: float) -> BaseChatModel:
        """构建 ChatQwen 模型实例。"""
        return ChatQwen(
            model=self.model_name,
            api_key=config.dashscope_api_key,
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
        try:
            await self._initialize_agent()

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（非流式）: {question}")

            # 构建消息列表（系统提示词由 create_agent 接管）
            messages = [HumanMessage(content=question)]

            # 构建 Agent 输入
            agent_input = {"messages": messages}

            # 配置 thread_id（用于会话持久化）
            config_dict = {
                "configurable": {
                    "thread_id": session_id
                }
            }

            result = await self.agent.ainvoke(
                input=agent_input,
                config=config_dict,
            )

            # 提取最终答案
            messages_result = result.get("messages", [])
            if messages_result:
                last_message = messages_result[-1]
                answer = last_message.content if hasattr(last_message, 'content') else str(last_message)

                # 记录工具调用
                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    tool_names = [tc.get("name", "unknown") for tc in last_message.tool_calls]
                    logger.info(f"[会话 {session_id}] Agent 调用了工具: {tool_names}")

                logger.info(f"[会话 {session_id}] RAG Agent 查询完成（非流式）")
                return answer

            logger.warning(f"[会话 {session_id}] Agent 返回结果为空")
            return ""

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

            # 构建消息列表（系统提示词由 create_agent 接管）
            messages = [HumanMessage(content=question)]

            # 构建 Agent 输入
            agent_input = {"messages": messages}

            # 配置 thread_id（用于会话持久化）
            config_dict = {
                "configurable": {
                    "thread_id": session_id
                }
            }

            async for token, metadata in self.agent.astream(
                input=agent_input,
                config=config_dict,
                stream_mode="messages",
            ):
                node_name = metadata.get('langgraph_node', 'unknown') if isinstance(metadata, dict) else 'unknown'
                message_type = type(token).__name__

                if message_type in ("AIMessage", "AIMessageChunk"):
                    content_blocks = getattr(token, 'content_blocks', None)

                    if content_blocks and isinstance(content_blocks, list):
                        for block in content_blocks:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                text_content = block.get('text', '')
                                if text_content:
                                    yield {
                                        "type": "content",
                                        "data": text_content,
                                        "node": node_name
                                    }

            logger.info(f"[会话 {session_id}] RAG Agent 查询完成（流式）")
            yield {"type": "complete"}

        except Exception as e:
            logger.error(
                "[会话 {}] RAG Agent 查询失败（流式）: {}",
                session_id,
                self._format_exception(e),
            )
            yield {
                "type": "error",
                "data": self._format_exception(e)
            }
            raise

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
rag_agent_service = RagAgentService(streaming=True)
