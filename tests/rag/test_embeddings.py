from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError

from app.core.config import Settings
from app.rag.embeddings import CachedQueryEmbedder, HashEmbeddingProvider, OpenAIEmbeddingProvider


async def test_hash_deterministic_normalized_and_lexical() -> None:
    provider = HashEmbeddingProvider(Settings(embedding_dimensions=1536))
    a, b, unrelated = await provider.embed_documents(
        [
            "garantia guitarra instrumento",
            "garantia instrumento musical",
            "bananas aeroportos espaciais",
        ]
    )
    assert a == await provider.embed_query("garantia guitarra instrumento")
    assert math.sqrt(sum(v * v for v in a)) == pytest.approx(1)
    assert sum(x * y for x, y in zip(a, b, strict=True)) > sum(
        x * y for x, y in zip(a, unrelated, strict=True)
    )
    with pytest.raises(ValueError, match="production"):
        HashEmbeddingProvider(Settings().model_copy(update={"app_env": "prod"}))


async def test_cache_hit_and_redis_degradation(caplog: pytest.LogCaptureFixture) -> None:
    provider = HashEmbeddingProvider()
    provider.embed_query = AsyncMock(wraps=provider.embed_query)
    cache = AsyncMock()
    cache.get.return_value = None
    cached = CachedQueryEmbedder(provider, cache, ttl_seconds=123, key=b"cache-secret")
    result = await cached.embed_query("trocas")
    key, value = cache.set.call_args.args
    assert "trocas" not in key
    assert cache.set.call_args.kwargs == {"ex": 123}
    cache.get.return_value = value
    assert await cached.embed_query("trocas") == result
    assert provider.embed_query.call_count == 1
    cache.get.side_effect = RuntimeError("never log private@example.com")
    cache.set.side_effect = RuntimeError("never log private@example.com")
    assert await cached.embed_query("trocas") == result
    assert "private@example.com" not in caplog.text
    assert "cache_read_failed" in caplog.text
    assert "cache_write_failed" in caplog.text


async def test_openai_batches_retries_dimensions_and_privacy() -> None:
    response = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0, 0.0])])
    create = AsyncMock(
        side_effect=[
            APIConnectionError(request=httpx.Request("POST", "https://example.invalid")),
            response,
            response,
        ]
    )
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    provider = OpenAIEmbeddingProvider(
        Settings(embedding_dimensions=2), client=client, batch_size=1
    )
    assert await provider.embed_documents(["garantia", "trocas"]) == [[1.0, 0.0], [1.0, 0.0]]
    assert create.call_count == 3
    assert create.call_args.kwargs["dimensions"] == 2
    with pytest.raises(ValueError, match="privacy"):
        await provider.embed_query("cliente@example.com")
    assert create.call_count == 3


def test_query_cache_key_is_hmac_not_plain_hash() -> None:
    """Review L-5: the key is an HMAC of model|text, never sha256(model + text)."""
    import hashlib
    import hmac

    provider = HashEmbeddingProvider()
    a = CachedQueryEmbedder(provider, AsyncMock(), key=b"secret-a")
    b = CachedQueryEmbedder(provider, AsyncMock(), key=b"secret-b")
    text = "qual o prazo de entrega?"
    expected = hmac.new(
        b"secret-a", f"{provider.model_name}|{text}".encode(), hashlib.sha256
    ).hexdigest()
    assert a.cache_key(text) == expected
    assert a.cache_key(text) != b.cache_key(text)
    plain = hashlib.sha256((provider.model_name + "\0" + text).encode()).hexdigest()
    assert plain not in {a.cache_key(text), b.cache_key(text)}
    with pytest.raises(ValueError):
        CachedQueryEmbedder(provider, AsyncMock(), key=b"")


async def test_get_embedding_provider_uses_user_ref_key() -> None:
    from pydantic import SecretStr

    from app.rag.embeddings import QUERY_CACHE_PREFIX, get_embedding_provider

    settings = Settings(
        app_env="test", embedding_provider="hash", user_ref_key=SecretStr("ref-key")
    )
    embedder = get_embedding_provider(settings)
    assert isinstance(embedder, CachedQueryEmbedder)
    reference = CachedQueryEmbedder(HashEmbeddingProvider(settings), AsyncMock(), key=b"ref-key")
    assert embedder.cache_key("x") == reference.cache_key("x")
    assert QUERY_CACHE_PREFIX == "rag:query:"
