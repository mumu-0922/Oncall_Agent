"""Lightweight model provider registry.

This borrows the useful part of nanobot's provider architecture: keep provider
metadata in one place and make business code depend on a factory instead of a
specific vendor SDK.  It intentionally stays small because Oncall_Agent only
needs OpenAI-compatible chat/embedding switching, not nanobot's full multi-
backend runtime.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    """Metadata for one OpenAI-compatible model provider."""

    name: str
    display_name: str
    default_api_base: str = ""
    is_gateway: bool = False
    is_local: bool = False
    requires_api_key: bool = True
    detect_by_base_keyword: str = ""


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="dashscope",
        display_name="DashScope",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        detect_by_base_keyword="dashscope.aliyuncs.com",
    ),
    ProviderSpec(
        name="openai",
        display_name="OpenAI",
        default_api_base="https://api.openai.com/v1",
        detect_by_base_keyword="api.openai.com",
    ),
    ProviderSpec(
        name="openai_compatible",
        display_name="OpenAI-compatible Gateway",
        is_gateway=True,
    ),
    ProviderSpec(
        name="custom",
        display_name="Custom OpenAI-compatible Endpoint",
        is_gateway=True,
    ),
    ProviderSpec(
        name="openrouter",
        display_name="OpenRouter",
        default_api_base="https://openrouter.ai/api/v1",
        is_gateway=True,
        detect_by_base_keyword="openrouter.ai",
    ),
    ProviderSpec(
        name="aihubmix",
        display_name="AiHubMix",
        default_api_base="https://aihubmix.com/v1",
        is_gateway=True,
        detect_by_base_keyword="aihubmix",
    ),
    ProviderSpec(
        name="siliconflow",
        display_name="SiliconFlow",
        default_api_base="https://api.siliconflow.cn/v1",
        is_gateway=True,
        detect_by_base_keyword="siliconflow",
    ),
    ProviderSpec(
        name="deepseek",
        display_name="DeepSeek",
        default_api_base="https://api.deepseek.com/v1",
        detect_by_base_keyword="deepseek",
    ),
    ProviderSpec(
        name="ollama",
        display_name="Ollama",
        default_api_base="http://localhost:11434/v1",
        is_local=True,
        requires_api_key=False,
        detect_by_base_keyword="localhost:11434",
    ),
    ProviderSpec(
        name="lm_studio",
        display_name="LM Studio",
        default_api_base="http://localhost:1234/v1",
        is_local=True,
        requires_api_key=False,
        detect_by_base_keyword="localhost:1234",
    ),
    ProviderSpec(
        name="vllm",
        display_name="vLLM",
        default_api_base="http://localhost:8000/v1",
        is_local=True,
        requires_api_key=False,
        detect_by_base_keyword="localhost:8000",
    ),
)

_PROVIDER_BY_NAME = {provider.name: provider for provider in PROVIDERS}


def find_provider(name: str | None) -> ProviderSpec | None:
    """Find a provider by normalized name."""
    normalized = (name or "").strip().lower().replace("-", "_")
    return _PROVIDER_BY_NAME.get(normalized)


def resolve_provider(name: str | None, api_base: str | None = None) -> ProviderSpec:
    """Resolve an explicit provider name, or infer a known provider from api_base."""
    explicit = find_provider(name)
    if explicit:
        return explicit

    normalized_base = (api_base or "").strip().lower()
    if normalized_base:
        for provider in PROVIDERS:
            if provider.detect_by_base_keyword and provider.detect_by_base_keyword in normalized_base:
                return provider
        return _PROVIDER_BY_NAME["openai_compatible"]

    return _PROVIDER_BY_NAME["openai_compatible"]
