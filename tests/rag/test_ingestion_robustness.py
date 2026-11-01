"""Review L-12a (stuck `processing` versions) and M-9 (parsing off the event loop)."""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentVersion
from app.models.base import utcnow
from app.rag import ingestion
from app.rag.embeddings import HashEmbeddingProvider
from app.rag.ingestion import (
    create_document_version,
    is_stale_processing,
    process_document_version,
)

pytestmark = pytest.mark.integration


async def _pending(session: AsyncSession, tenant: uuid.UUID, text: str) -> uuid.UUID:
    receipt = await create_document_version(
        session,
        tenant_id=tenant,
        title="Garantia",
        data=text.encode(),
        filename="garantia.md",
        source_type="md",
    )
    return receipt.version_id


async def _status(session: AsyncSession, version_id: uuid.UUID) -> DocumentVersion:
    version = await session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.id == version_id)
        .execution_options(populate_existing=True)
    )
    assert version is not None
    return version


@pytest.mark.parametrize(("age_minutes", "reprocessed"), [(20, True), (5, False)])
async def test_stuck_processing_version_can_be_reprocessed(
    session: AsyncSession,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
    age_minutes: int,
    reprocessed: bool,
) -> None:
    tenant, _ = two_tenants
    version_id = await _pending(session, tenant, f"# Garantia\nUm ano de garantia {age_minutes}.")
    # Simulate a worker that died mid-job `age_minutes` ago.
    await session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.id == version_id)
        .values(
            ingestion_status="processing",
            processed_at=utcnow() - timedelta(minutes=age_minutes),
        )
    )
    await session.commit()
    version = await _status(session, version_id)
    assert is_stale_processing(version) is reprocessed

    await process_document_version(
        session, tenant_id=tenant, version_id=version_id, embedder=HashEmbeddingProvider()
    )
    version = await _status(session, version_id)
    assert version.ingestion_status == ("completed" if reprocessed else "processing")


async def test_processing_start_is_stamped(
    session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """A version completed long ago and reset by /reprocess is not 'stale' while running."""
    tenant, _ = two_tenants
    version_id = await _pending(session, tenant, "# Trocas\nSete dias para troca.")
    await session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.id == version_id)
        .values(processed_at=utcnow() - timedelta(days=3))
    )
    await session.commit()
    seen: list[bool] = []

    class Spy(HashEmbeddingProvider):
        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            version = await _status(session, version_id)
            seen.append(version.ingestion_status == "processing" and is_stale_processing(version))
            return await super().embed_documents(texts)

    await process_document_version(session, tenant_id=tenant, version_id=version_id, embedder=Spy())
    assert seen == [False]


async def test_extraction_runs_in_worker_thread(
    session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant, _ = two_tenants
    main_thread = threading.get_ident()
    threads: list[int] = []
    real_extract = ingestion.extract_document

    def spy(*args: object, **kwargs: object) -> object:
        threads.append(threading.get_ident())
        return real_extract(*args, **kwargs)  # type: ignore[arg-type]

    with patch.object(ingestion, "extract_document", spy):
        version_id = await _pending(session, tenant, "# Frete\nFrete grátis acima de 500.")
        await process_document_version(
            session, tenant_id=tenant, version_id=version_id, embedder=HashEmbeddingProvider()
        )
    assert len(threads) == 2  # validation in create + extraction in process
    assert main_thread not in threads


async def test_nul_characters_are_stripped_from_document_metadata(
    session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Adversarial FINDING-6 (defence in depth): PostgreSQL text cannot store NUL, so a
    caller that bypasses the API validation must not get a database error."""
    tenant, _ = two_tenants
    receipt = await create_document_version(
        session,
        tenant_id=tenant,
        title="Polí\x00tica",
        data=b"Trocas em 7 dias.",
        filename="tro\x00cas.txt",
        source_type="txt",
        category="pol\x00iticas",
        source_url="https://example.test/\x00trocas",
    )
    document = await session.scalar(select(Document).where(Document.id == receipt.document_id))
    assert document is not None
    assert document.title == "Política" and document.filename == "trocas.txt"
    assert document.category == "politicas"
    assert document.source_url == "https://example.test/trocas"
