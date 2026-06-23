"""LLM factory for OpenAI-compatible chat models.

业务代码只依赖这里，不直接绑定 ChatQwen / 某个厂商 SDK。这样用户有 GPT
中转时只需要改 `.env` 的 LLM_*，不用改代码。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from loguru import logger

from app.config import config
from app.core.model_provider_registry import ProviderSpec, resolve_provider


class LLMConfigurationError(ValueError):
    """Raised when chat model configuration is incomplete."""


def _mask_secret(value: str) -> str:
    """Mask secrets for logs."""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


class LLMFactory:
    """Create LangChain chat models through OpenAI-compatible endpoints."""

    @staticmethod
    def create_chat_model(
        model: str | None = None,
        temperature: float = 0.7,
        streaming: bool = True,
        base_url: str | None = None,
        api_key: str | None = None,
        provider: str | None = None,
    ) -> ChatOpenAI:
        """Create a chat model from explicit args or global config."""
        resolved_model = (model or config.effective_llm_model).strip()
        resolved_base_url = LLMFactory._normalize_base_url(base_url or config.effective_llm_api_base)
        resolved_api_key = (api_key or config.effective_llm_api_key).strip()
        resolved_provider = resolve_provider(provider or config.effective_llm_provider, resolved_base_url)

        LLMFactory._validate_config(
            provider=resolved_provider,
            model=resolved_model,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
        )

        logger.info(
            "初始化 ChatModel provider={} model={} base_url={} api_key={}",
            resolved_provider.name,
            resolved_model,
            resolved_base_url,
            _mask_secret(resolved_api_key),
        )

        return ChatOpenAI(
            model=resolved_model,
            temperature=temperature,
            streaming=streaming and getattr(config, "llm_streaming", False),
            base_url=resolved_base_url,
            api_key=resolved_api_key or "local-no-key",
            timeout=getattr(config, "llm_timeout_seconds", 60.0),
            max_retries=getattr(config, "llm_max_retries", 1),
        )

    @staticmethod
    def _normalize_base_url(base_url: str | None) -> str:
        """Normalize OpenAI-compatible base URL.

        Many gateways publish host-only URLs but LangChain/OpenAI expects the
        API root, usually ending in /v1.
        """
        normalized = (base_url or "").strip().rstrip("/")
        if not normalized:
            return ""
        if normalized.endswith("/v1") or "/v1/" in normalized:
            return normalized
        return f"{normalized}/v1"

    @staticmethod
    def _validate_config(
        *,
        provider: ProviderSpec,
        model: str,
        base_url: str,
        api_key: str,
    ) -> None:
        if not model:
            raise LLMConfigurationError("LLM model is empty. Please set LLM_MODEL or RAG_MODEL.")
        if not base_url:
            raise LLMConfigurationError("LLM api base is empty. Please set LLM_API_BASE.")
        if provider.requires_api_key and config._is_placeholder_secret(api_key):
            raise LLMConfigurationError(
                "LLM api key is missing or placeholder. Please set LLM_API_KEY."
            )


# 全局 LLM 工厂实例
llm_factory = LLMFactory()
