"""LangChain embeddings contract, with every provider replaced by local fakes."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.embeddings import Embeddings

from app.core.config import Settings
from app.rag.embeddings import (
    CachedQueryEmbedder,
    LangChainEmbeddingProvider,
    get_embedding_provider,
)


class RecordingEmbeddings(Embeddings):
    def __init__(self, dimensions=1536, delay=0):
        self.dimensions = dimensions
        self.documents = []
        self.queries = []
        self.delay = delay

    def embed_documents(self, texts):
        raise AssertionError("Only async API is expected")

    def embed_query(self, text):
        raise AssertionError("Only async API is expected")

    async def aembed_documents(self, texts):
        self.documents.append(texts)
        if self.delay:
            await asyncio.sleep(self.delay)
        return [[0.0] * self.dimensions for _ in texts]

    async def aembed_query(self, text):
        self.queries.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        return [0.0] * self.dimensions


def config(**kwargs):
    return Settings(
        embedding_provider="langchain", langchain_embedding_model="openai:offline", **kwargs
    )


async def test_batches_and_separate_async_query_api():
    fake = RecordingEmbeddings()
    provider = LangChainEmbeddingProvider(config(), client=fake, batch_size=2)
    vectors = await provider.embed_documents(["guitarra", "violão", "piano"])
    assert len(vectors) == 3 and all(len(vector) == 1536 for vector in vectors)
    assert fake.documents == [["guitarra", "violão"], ["piano"]]
    assert await provider.embed_query("trocas") == [0.0] * 1536
    assert fake.queries == ["trocas"]
    assert await provider.embed_documents([]) == []
    assert provider.model_name == "langchain:openai:offline"


@pytest.mark.parametrize("method", ["query", "documents"])
async def test_restricted_payload_blocked_before_any_batch(method):
    fake = RecordingEmbeddings()
    provider = LangChainEmbeddingProvider(config(), client=fake, batch_size=1)
    with pytest.raises(ValueError, match="privacy"):
        if method == "query":
            await provider.embed_query("ana@example.test")
        else:
            await provider.embed_documents(["guitarra", "ana@example.test"])
    assert fake.documents == [] and fake.queries == []


@pytest.mark.parametrize("method", ["query", "documents"])
async def test_dimension_mismatch_is_configuration_error(method):
    fake = RecordingEmbeddings(dimensions=768)
    provider = LangChainEmbeddingProvider(config(), client=fake, batch_size=1)
    with pytest.raises(ValueError, match=r"expected 1536, received 768.*migration"):
        if method == "query":
            await provider.embed_query("trocas")
        else:
            await provider.embed_documents(["trocas", "garantia"])
    assert len(fake.queries) + len(fake.documents) == 1


@pytest.mark.parametrize("name", ["anthropic:test", "google:test", "missing-prefix", "ollama:"])
def test_unsupported_embedding_provider(name):
    with pytest.raises(ValueError, match="Unsupported LangChain embedding model"):
        LangChainEmbeddingProvider(Settings(langchain_embedding_model=name))


@pytest.mark.parametrize("method", ["query", "documents"])
async def test_embedding_timeout(method):
    provider = LangChainEmbeddingProvider(
        config(model_timeout_seconds=0.01), client=RecordingEmbeddings(delay=1)
    )
    with pytest.raises(TimeoutError):
        if method == "query":
            await provider.embed_query("trocas")
        else:
            await provider.embed_documents(["trocas"])


def test_factory_preserves_query_cache(monkeypatch):
    fake = LangChainEmbeddingProvider(config(), client=RecordingEmbeddings())
    monkeypatch.setattr("app.rag.embeddings.LangChainEmbeddingProvider", lambda settings: fake)
    cached = get_embedding_provider(config())
    assert isinstance(cached, CachedQueryEmbedder)
    assert cached.provider is fake and cached.dimensions == 1536
