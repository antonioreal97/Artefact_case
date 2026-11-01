from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.rag.citations import build_citations, format_sources_footer
from app.rag.types import RetrievedChunk
from app.services import ingestion_jobs


def chunk(*, score: float, title: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        document_title=title,
        category=None,
        source_url=None,
        section_title="4 Trocas e Devoluções",
        page_start=3,
        page_end=4,
        content="Texto de exemplo " * 30,
        vector_similarity=0.9,
        text_rank=None,
        score=score,
    )


def test_citations_preserve_first_seen_order_across_searches() -> None:
    # Contract clarification: callers can append results from subsequent searches;
    # labels already shown to the model must never shift due to higher new scores.
    first = chunk(score=0.01, title="Políticas da Loja")
    second = chunk(score=0.9, title="Garantia")
    citations = build_citations([first, second, first])
    assert [c.chunk_id for c in citations] == [first.chunk_id, second.chunk_id]
    assert [c.label for c in citations] == ["S1", "S2"]
    assert citations[0].page == 3 and len(citations[0].quote) == 240
    assert (
        format_sources_footer(citations[:1])
        == "Fontes:\n[S1] Políticas da Loja — §4 Trocas e Devoluções, p. 3"
    )
    assert format_sources_footer([]) == ""


async def test_inline_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(ingestion_mode="inline")
    monkeypatch.setattr(ingestion_jobs, "get_settings", lambda: settings)
    task = AsyncMock()
    monkeypatch.setattr(ingestion_jobs, "ingest_document_version", task)
    tenant, version = uuid.uuid4(), uuid.uuid4()
    await ingestion_jobs.enqueue_ingestion(tenant, version)
    task.assert_awaited_once_with({}, tenant, version)


async def test_queue_dispatch_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(ingestion_mode="queue")
    monkeypatch.setattr(ingestion_jobs, "get_settings", lambda: settings)
    redis = AsyncMock()
    pool = AsyncMock(return_value=redis)
    monkeypatch.setattr(ingestion_jobs, "create_pool", pool)
    tenant, version = uuid.uuid4(), uuid.uuid4()
    await ingestion_jobs.enqueue_ingestion(tenant, version)
    redis.enqueue_job.assert_awaited_once_with(
        "ingest_document_version", str(tenant), str(version), _job_id=f"ingest:{tenant}:{version}"
    )
    redis.aclose.assert_awaited_once()
