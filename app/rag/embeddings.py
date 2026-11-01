"""Embedding providers. Hash vectors are a lexical test baseline, not semantic AI."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import re
import unicodedata
from collections import Counter
from typing import Protocol

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.rag.types import EmbeddingProvider
from emporio_agent.privacy import DataClassification, assert_external_payload_safe

logger = logging.getLogger(__name__)


class HashEmbeddingProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        if settings.app_env == "prod":
            raise ValueError("Hash embeddings are forbidden in production")
        self.dimensions = settings.embedding_dimensions
        self.model_name = f"hash-lexical-v1-{self.dimensions}"

    def _embed(self, text: str) -> list[float]:
        folded = "".join(
            c
            for c in unicodedata.normalize("NFKD", text.casefold())
            if not unicodedata.combining(c)
        )
        words = re.findall(r"\w+", folded)
        features: Counter[str] = Counter()
        for word in words:
            features[f"w:{word}"] += 3
            padded = f"^{word}$"
            for n in (3, 4):
                for i in range(len(padded) - n + 1):
                    features[f"c:{padded[i : i + n]}"] += 1
        vector = [0.0] * self.dimensions
        for feature, count in features.items():
            digest = hashlib.sha256(feature.encode()).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            vector[index] += (1 if digest[8] & 1 else -1) * math.sqrt(count)
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncOpenAI | None = None,
        batch_size: int = 64,
        max_attempts: int = 3,
    ) -> None:
        self.model_name = settings.openai_embedding_model
        self.dimensions = settings.embedding_dimensions
        self.batch_size = max(1, batch_size)
        self.max_attempts = max(1, max_attempts)
        self.client = client or AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value() if settings.openai_api_key else None,
            timeout=settings.model_timeout_seconds,
            max_retries=0,
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Callers must redact tenant-specific identifiers locally before this boundary.
        assert_external_payload_safe(texts, classification=DataClassification.PUBLIC)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            for attempt in range(self.max_attempts):
                try:
                    result = await self.client.embeddings.create(
                        model=self.model_name,
                        input=texts[start : start + self.batch_size],
                        dimensions=self.dimensions,
                        encoding_format="float",
                    )
                    batch = [
                        entry.embedding
                        for entry in sorted(result.data, key=lambda item: item.index)
                    ]
                    if len(batch) != len(texts[start : start + self.batch_size]) or any(
                        len(v) != self.dimensions for v in batch
                    ):
                        raise ValueError("Invalid embedding response")
                    vectors.extend(batch)
                    break
                except (APIConnectionError, RateLimitError, APIStatusError) as exc:
                    retryable = (
                        not isinstance(exc, APIStatusError)
                        or exc.status_code in (408, 409, 429)
                        or exc.status_code >= 500
                    )
                    if not retryable or attempt + 1 == self.max_attempts:
                        raise
                    await asyncio.sleep(0.25 * 2**attempt)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


class QueryCache(Protocol):
    async def get(self, key: str) -> str | bytes | None: ...
    async def set(self, key: str, value: str, *, ex: int) -> object: ...


QUERY_CACHE_PREFIX = "rag:query:"


class CachedQueryEmbedder:
    """Redis cache for query embeddings.

    Key: ``rag:query:`` + HMAC-SHA256(secret, model | text) (review L-5) - no raw or unsalted
    hash of the customer's question is stored, so cached keys cannot be matched against a
    dictionary of likely questions without the secret.

    Residual risk (accepted, documented): the key is NOT tenant-scoped. A query embedding is
    a tenant-neutral function of (model, text) and holds no tenant data, so sharing it is
    not a data leak; the only side channel is cache-hit latency revealing that *someone*
    asked the identical text within the TTL. ``embed_query`` has no tenant parameter in the
    frozen ``EmbeddingProvider`` contract (app/rag/types.py).
    """

    def __init__(
        self,
        provider: EmbeddingProvider,
        redis: QueryCache,
        ttl_seconds: int = 3600,
        *,
        key: bytes,
    ) -> None:
        if not key:
            raise ValueError("A non-empty HMAC key is required for the query cache")
        self._key = key
        self.provider = provider
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self.model_name = provider.model_name
        self.dimensions = provider.dimensions

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self.provider.embed_documents(texts)

    def cache_key(self, text: str) -> str:
        message = (self.model_name + "|" + text).encode("utf-8")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    async def embed_query(self, text: str) -> list[float]:
        key = QUERY_CACHE_PREFIX + self.cache_key(text)
        try:
            cached = await self.redis.get(key)
            if cached:
                value = json.loads(cached)
                if (
                    isinstance(value, list)
                    and len(value) == self.dimensions
                    and all(isinstance(v, (int, float)) and math.isfinite(v) for v in value)
                ):
                    return [float(v) for v in value]
        except Exception:
            # Never log Redis exceptions: connection URLs may carry credentials.
            logger.warning("query_embedding_cache_read_failed")
        embedding = await self.provider.embed_query(text)
        try:
            await self.redis.set(key, json.dumps(embedding), ex=self.ttl_seconds)
        except Exception:
            logger.warning("query_embedding_cache_write_failed")
        return embedding


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    provider: EmbeddingProvider = (
        HashEmbeddingProvider(settings)
        if settings.embedding_provider == "hash"
        else OpenAIEmbeddingProvider(settings)
    )
    redis = Redis.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1
    )
    secret = settings.user_ref_key.get_secret_value() or settings.api_key_pepper.get_secret_value()
    return CachedQueryEmbedder(
        provider, redis, settings.query_embedding_cache_ttl_seconds, key=secret.encode("utf-8")
    )
