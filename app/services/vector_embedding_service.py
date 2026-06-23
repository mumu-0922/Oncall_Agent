"""Embedding service based on LangChain's Embeddings interface.

Embedding is intentionally independent from Chat LLM.  When the user has a GPT
chat gateway but no embedding key, set ``EMBEDDING_PROVIDER=disabled`` and RAG
will keep parent-child docstore + BM25 while skipping dense Milvus indexing.
"""

from __future__ import annotations

from langchain_core.embeddings import Embeddings
from loguru import logger
from openai import OpenAI

from app.config import config


class EmbeddingDisabledError(RuntimeError):
    """Raised when dense embedding is requested while embedding is disabled."""


class DisabledEmbeddings(Embeddings):
    """Embeddings implementation that fails with a clear, catchable error."""

    def __init__(self, reason: str = "Embedding provider is disabled") -> None:
        self.reason = reason
        logger.warning("Embedding 已禁用: {}", reason)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingDisabledError(self.reason)

    def embed_query(self, text: str) -> list[float]:
        raise EmbeddingDisabledError(self.reason)


class OpenAICompatibleEmbeddings(Embeddings):
    """OpenAI-compatible text embedding client.

    Works with DashScope compatible mode, OpenAI, and most embedding gateways
    exposing ``POST /v1/embeddings``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int = 1024,
        provider: str = "openai_compatible",
    ) -> None:
        if config._is_placeholder_secret(api_key):
            raise ValueError("Embedding API key is missing or placeholder")
        if not base_url:
            raise ValueError("Embedding API base is empty")
        if not model:
            raise ValueError("Embedding model is empty")

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.dimensions = dimensions
        self.provider = provider

        logger.info(
            "Embeddings 初始化完成 provider={} model={} base_url={} dimensions={} api_key={}",
            provider,
            model,
            base_url,
            dimensions,
            self._mask_api_key(api_key),
        )

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        """Mask API Key for logs."""
        if len(api_key) > 8:
            return f"{api_key[:4]}...{api_key[-4:]}"
        return "***"

    def _request_kwargs(self, input_texts: str | list[str]) -> dict:
        payload = {
            "model": self.model,
            "input": input_texts,
            "encoding_format": "float",
        }
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        return payload

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents in batch."""
        if not texts:
            return []

        try:
            logger.info("批量嵌入 {} 个文档", len(texts))
            response = self.client.embeddings.create(**self._request_kwargs(texts))
            embeddings = [item.embedding for item in response.data]
            if embeddings:
                logger.debug("批量嵌入完成, 维度: {}", len(embeddings[0]))
            return embeddings
        except Exception as e:
            logger.error("批量嵌入失败: {}", e)
            raise RuntimeError(f"批量嵌入失败: {e}") from e

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")

        try:
            logger.debug("嵌入查询, 长度: {} 字符", len(text))
            response = self.client.embeddings.create(**self._request_kwargs(text))
            embedding = response.data[0].embedding
            logger.debug("查询嵌入完成, 维度: {}", len(embedding))
            return embedding
        except Exception as e:
            logger.error("查询嵌入失败: {}", e)
            raise RuntimeError(f"查询嵌入失败: {e}") from e


# Backward-compatible name for older imports/docs.
DashScopeEmbeddings = OpenAICompatibleEmbeddings


def create_embedding_service() -> Embeddings:
    """Create the configured embedding service.

    Missing/disabled embedding returns DisabledEmbeddings instead of crashing at
    import time.  Dense indexing/search callers decide whether to skip or report
    the unavailable dense path.
    """
    provider = config.effective_embedding_provider
    if not config.is_embedding_enabled:
        return DisabledEmbeddings(
            reason=(
                "EMBEDDING_PROVIDER=disabled or EMBEDDING_API_KEY is missing; "
                "dense vector indexing is skipped, BM25/docstore remains available."
            )
        )

    return OpenAICompatibleEmbeddings(
        provider=provider,
        api_key=config.effective_embedding_api_key,
        base_url=config.effective_embedding_api_base,
        model=config.effective_embedding_model,
        dimensions=config.embedding_dimensions,
    )


# 全局单例
vector_embedding_service = create_embedding_service()
