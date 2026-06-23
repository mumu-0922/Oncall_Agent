"""配置管理模块

使用 Pydantic Settings 实现类型安全的配置管理
"""

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用配置
    app_name: str = "SuperBizAgent"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 9900

    # DashScope 配置
    dashscope_api_key: str = ""  # 默认空字符串，实际使用需从环境变量加载
    dashscope_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen-max"
    dashscope_embedding_model: str = "text-embedding-v4"  # v4 支持多种维度（默认 1024）

    # OpenAI / OpenAI-compatible 通用配置
    # 参考 nanobot 的 provider/factory 思路：业务代码只依赖统一工厂，不直接绑定厂商 SDK。
    # 常见用法：
    #   LLM_PROVIDER=openai_compatible
    #   LLM_API_BASE=https://your-gateway.example/v1
    #   LLM_API_KEY=sk-...
    #   LLM_MODEL=gpt-4o-mini
    llm_provider: str = ""  # auto by config | dashscope | openai | openai_compatible | custom
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_model: str = ""
    llm_streaming: bool = False
    llm_timeout_seconds: float = 180.0
    llm_max_retries: int = 2
    openai_api_key: str = ""
    openai_api_base: str = "https://api.openai.com/v1"

    # Embedding 独立配置：没有 embedding key 时默认 disabled，让 RAG 走 BM25-only 降级。
    embedding_provider: str = "disabled"  # dashscope | openai | openai_compatible | disabled
    embedding_api_key: str = ""
    embedding_api_base: str = ""
    embedding_model: str = ""
    embedding_dimensions: int = 1024

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 3
    rag_model: str = ""  # 兼容旧配置；为空时使用 LLM_MODEL / DASHSCOPE_MODEL
    rag_context_summary_enabled: bool = True
    rag_summary_trigger_messages: int = 12
    rag_summary_keep_messages: int = 6
    rag_summary_trim_tokens: int = 4000
    rag_retrieval_mode: str = "hybrid_parent"  # dense | hybrid | hybrid_parent
    rag_docstore_dir: str = "data/rag"
    rag_parent_max_chars: int = 3500
    rag_child_chunk_size: int = 500
    rag_child_chunk_overlap: int = 80
    rag_dense_fetch_k: int = 10
    rag_bm25_fetch_k: int = 10
    rag_final_top_k: int = 3
    rag_dense_weight: float = 0.6
    rag_bm25_weight: float = 0.4
    rag_expand_parent: bool = True
    rag_parent_context_max_chars: int = 2500

    # 文档分块配置
    chunk_max_size: int = 800
    chunk_overlap: int = 100

    # AIOps Agent 配置
    # AIOps 是证据驱动链路：默认强制 MCP 工具可用，并要求执行步骤至少触发一次工具调用。
    aiops_structured_output_method: str = "function_calling"  # function_calling | json_mode | json_schema
    aiops_require_tool_call: bool = True
    aiops_tool_call_max_rounds: int = 3
    aiops_executor_summarize_with_llm: bool = False

    # MCP 服务配置
    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"

    @staticmethod
    def _clean(value: str | None) -> str:
        """Normalize string env values."""
        return (value or "").strip()

    @staticmethod
    def _is_placeholder_secret(value: str | None) -> bool:
        """Detect obvious placeholder keys without logging secrets."""
        normalized = Settings._clean(value).lower()
        return normalized in {
            "",
            "your-api-key",
            "your-api-key-here",
            "your-llm-api-key",
            "your-openai-api-key",
            "your-dashscope-api-key",
            "sk-...",
        }

    @property
    def effective_llm_provider(self) -> str:
        """Return the active chat model provider."""
        explicit = self._clean(self.llm_provider).lower()
        if explicit:
            return explicit
        if self._clean(self.llm_api_base) or self._clean(self.llm_api_key) or self._clean(self.llm_model):
            return "openai_compatible"
        if not self._is_placeholder_secret(self.openai_api_key):
            return "openai"
        return "dashscope"

    @property
    def effective_llm_api_key(self) -> str:
        """Return the active chat model API key."""
        provider = self.effective_llm_provider
        if self._clean(self.llm_api_key):
            return self._clean(self.llm_api_key)
        if provider == "dashscope":
            return self._clean(self.dashscope_api_key)
        if provider == "openai":
            return self._clean(self.openai_api_key)
        return self._clean(self.openai_api_key)

    @property
    def effective_llm_api_base(self) -> str:
        """Return the active chat model OpenAI-compatible base URL."""
        provider = self.effective_llm_provider
        if self._clean(self.llm_api_base):
            return self._clean(self.llm_api_base)
        if provider == "dashscope":
            return self._clean(self.dashscope_api_base)
        if provider == "openai":
            return self._clean(self.openai_api_base)
        return self._clean(self.openai_api_base)

    @property
    def effective_llm_model(self) -> str:
        """Return the active chat model name.

        LLM_MODEL intentionally wins over RAG_MODEL to make provider switching
        one-step for users with GPT gateways. RAG_MODEL remains as old-config
        compatibility.
        """
        return (
            self._clean(self.llm_model)
            or self._clean(self.rag_model)
            or self._clean(self.dashscope_model)
        )

    @property
    def effective_embedding_provider(self) -> str:
        """Return the active embedding provider."""
        return self._clean(self.embedding_provider).lower() or "disabled"

    @property
    def effective_embedding_api_key(self) -> str:
        """Return the active embedding API key."""
        provider = self.effective_embedding_provider
        if self._clean(self.embedding_api_key):
            return self._clean(self.embedding_api_key)
        if provider == "dashscope":
            return self._clean(self.dashscope_api_key)
        if provider == "openai":
            return self._clean(self.openai_api_key)
        return self._clean(self.openai_api_key)

    @property
    def effective_embedding_api_base(self) -> str:
        """Return the active embedding OpenAI-compatible base URL."""
        provider = self.effective_embedding_provider
        if self._clean(self.embedding_api_base):
            return self._clean(self.embedding_api_base)
        if provider == "dashscope":
            return self._clean(self.dashscope_api_base)
        if provider == "openai":
            return self._clean(self.openai_api_base)
        return self._clean(self.openai_api_base)

    @property
    def effective_embedding_model(self) -> str:
        """Return the active embedding model name."""
        if self.effective_embedding_provider in {"disabled", "none", "off", "false"}:
            return ""
        return self._clean(self.embedding_model) or self._clean(self.dashscope_embedding_model)

    @property
    def is_embedding_enabled(self) -> bool:
        """Whether dense embedding / Milvus indexing should be attempted."""
        provider = self.effective_embedding_provider
        if provider in {"disabled", "none", "off", "false"}:
            return False
        return not self._is_placeholder_secret(self.effective_embedding_api_key)

    @property
    def mcp_servers(self) -> dict[str, dict[str, Any]]:
        """获取完整的 MCP 服务器配置"""
        return {
            "cls": {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            },
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            }
        }


# 全局配置实例
config = Settings()
