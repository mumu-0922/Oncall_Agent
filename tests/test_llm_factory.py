from app.core.llm_factory import LLMConfigurationError, LLMFactory
from app.core.model_provider_registry import resolve_provider


class DummyConfig:
    effective_llm_provider = "openai_compatible"
    effective_llm_model = "gpt-test"
    effective_llm_api_base = "https://gateway.example/v1"
    effective_llm_api_key = "sk-test-123456"
    llm_streaming = False
    llm_timeout_seconds = 12.5
    llm_max_retries = 2

    @staticmethod
    def _is_placeholder_secret(value):
        return not value or value in {"your-api-key-here", "sk-..."}


def test_resolve_provider_detects_known_base():
    provider = resolve_provider("", "https://openrouter.ai/api/v1")

    assert provider.name == "openrouter"
    assert provider.is_gateway is True


def test_llm_factory_uses_openai_compatible_config(monkeypatch):
    monkeypatch.setattr("app.core.llm_factory.config", DummyConfig)

    llm = LLMFactory.create_chat_model(streaming=False, temperature=0)

    assert llm.model_name == "gpt-test"
    assert str(llm.openai_api_base).rstrip("/") == "https://gateway.example/v1"
    assert llm.request_timeout == 12.5
    assert llm.max_retries == 2


def test_llm_factory_rejects_missing_key(monkeypatch):
    class MissingKeyConfig(DummyConfig):
        effective_llm_api_key = ""

    monkeypatch.setattr("app.core.llm_factory.config", MissingKeyConfig)

    try:
        LLMFactory.create_chat_model(streaming=False)
    except LLMConfigurationError as exc:
        assert "LLM api key" in str(exc)
    else:
        raise AssertionError("missing key should raise LLMConfigurationError")
