"""Tenant-scoped, versioned ingestion with local PII minimization.

PII is irreversibly replaced with redact_for_log-style [REDACTED_*] markers
before chunking, storage and embedding (scanning before splitting prevents boundary
bypasses). Public merchant contacts are preserved in stored chunks but replaced
with [PUBLIC_CONTACT] for embedding, whose final egress guard has no allowlist.
Only error categories are logged: parser/provider exceptions can contain raw PII.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentSettings,
    AuditEvent,
    Chunk,
    CommerceCustomer,
    CommerceOrder,
    Document,
    DocumentVersion,
)
from app.models.base import utcnow
from app.rag.chunking import chunk_blocks, tokenizer
from app.rag.extraction import MIME_TYPES, TextBlock, extract_document
from app.rag.types import EmbeddingProvider, IngestionReceipt
from emporio_agent.privacy import find_restricted_data, redact_for_log

logger = logging.getLogger(__name__)
INJECTION = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|system\s+prompt|voc[eê]\s+agora\s+[eé]|ignore\s+(?:as\s+)?instru[çc][õo]es|<\|(?:system|im_start)\|>|\[INST\]",
    re.IGNORECASE,
)
CONTEXTUAL_PII = re.compile(
    r"(?im)\b(?:nome(?:\s+do\s+cliente)?|cliente|endere[çc]o|destinat[aá]rio|cpf|entrega(?:\s+prevista)?)\s*:\s*[^\n]+|\b(?:rua|avenida|av\.)\s+[^\n,]+,?\s+\d+[^\n]*"
)


def _receipt(version: DocumentVersion, *, duplicate: bool) -> IngestionReceipt:
    return IngestionReceipt(
        document_id=version.document_id,
        version_id=version.id,
        version=version.version,
        status=version.ingestion_status,
        duplicate=duplicate,
    )


def _transition(session: AsyncSession, version: DocumentVersion, status: str) -> None:
    version.ingestion_status = status
    session.add(
        AuditEvent(
            tenant_id=version.tenant_id,
            event_type="document_ingestion_status",
            resource_type="document_version",
            resource_id=str(version.id),
            data={"status": status},
        )
    )


async def create_document_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    title: str,
    data: bytes,
    filename: str | None,
    source_type: str,
    category: str | None = None,
    source_url: str | None = None,
    document_id: uuid.UUID | None = None,
) -> IngestionReceipt:
    # Full validation precedes persistence, including decoded-size/encryption checks.
    extract_document(data, filename=filename, source_type=source_type)
    if document_id is None:
        document = Document(
            tenant_id=tenant_id,
            title=title,
            filename=filename,
            source_type=source_type,
            category=category,
            source_url=source_url,
        )
        session.add(document)
        await session.flush()
    else:
        existing = await session.scalar(
            select(Document)
            .where(Document.id == document_id, Document.tenant_id == tenant_id)
            .with_for_update()
        )
        if existing is None:
            raise ValueError("Documento não encontrado.")
        document = existing
        # Source format belongs to the frozen Document contract, not each version.
        if document.source_type != source_type:
            raise ValueError("O formato de uma nova versão deve ser mantido.")
    digest = hashlib.sha256(data).hexdigest()
    duplicate = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.tenant_id == tenant_id,
            DocumentVersion.document_id == document.id,
            DocumentVersion.content_hash == digest,
        )
    )
    if duplicate is not None:
        receipt = _receipt(duplicate, duplicate=True)
        await session.commit()
        return receipt
    last = await session.scalar(
        select(func.max(DocumentVersion.version)).where(
            DocumentVersion.tenant_id == tenant_id, DocumentVersion.document_id == document.id
        )
    )
    version = DocumentVersion(
        tenant_id=tenant_id,
        document_id=document.id,
        version=(last or 0) + 1,
        content_hash=digest,
        mime_type=MIME_TYPES[source_type],
        byte_size=len(data),
        raw_content=data,
    )
    session.add(version)
    await session.flush()
    _transition(session, version, "pending")
    receipt = _receipt(version, duplicate=False)
    await session.commit()
    return receipt


async def _privacy_values(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[list[str], list[str]]:
    config = await session.scalar(select(AgentSettings).where(AgentSettings.tenant_id == tenant_id))
    public = list(config.public_contact_values) if config else []
    known: list[str] = []
    customers = await session.scalars(
        select(CommerceCustomer).where(CommerceCustomer.tenant_id == tenant_id)
    )
    for customer in customers:
        known.extend([customer.name, customer.email, customer.phone])
    orders = await session.scalars(
        select(CommerceOrder).where(CommerceOrder.tenant_id == tenant_id)
    )
    for order in orders:
        known.extend([str(order.external_id), order.tracking_code or "", order.notes or ""])
    return known, public


def redact_document_text(
    text: str, known: list[str], public: list[str], *, for_embedding: bool = False
) -> str:
    # Protect exact, administrator-approved public values, without logging them.
    replacements: dict[str, str] = {}
    for index, value in enumerate(sorted((v for v in public if v), key=len, reverse=True)):
        marker = f"PUBLICCONTACTTOKEN{chr(65 + index // 26)}{chr(65 + index % 26)}END"
        if marker in text:
            raise ValueError("Marcador reservado no documento.")
        text = re.sub(
            r"\s*".join(re.escape(char) for char in value if not char.isspace()),
            marker,
            text,
            flags=re.IGNORECASE,
        )
        replacements[marker] = "[PUBLIC_CONTACT]" if for_embedding else value
    if find_restricted_data(text, known_restricted_values=known):
        text = str(redact_for_log(text, known_restricted_values=known))
    text = CONTEXTUAL_PII.sub("[REDACTED_PERSONAL_DATA]", text)
    if find_restricted_data(text, known_restricted_values=known):
        raise ValueError("Documento não passou pela proteção de privacidade.")
    for marker, value in replacements.items():
        text = text.replace(marker, value)
    return text


async def process_document_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    embedder: EmbeddingProvider,
) -> None:
    version = await session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.id == version_id, DocumentVersion.tenant_id == tenant_id)
        .with_for_update()
    )
    if version is None:
        raise ValueError("Versão não encontrada.")
    if version.ingestion_status == "processing":
        await session.commit()
        return
    _transition(session, version, "processing")
    version.error_message = None
    await session.execute(
        delete(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.document_version_id == version_id)
    )
    await session.commit()
    try:
        # Refresh supports callers whose session expires instances on commit.
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id, DocumentVersion.tenant_id == tenant_id
            )
        )
        assert version is not None
        document = await session.scalar(
            select(Document).where(
                Document.id == version.document_id, Document.tenant_id == tenant_id
            )
        )
        if document is None:
            raise ValueError("Documento não encontrado.")
        blocks = extract_document(
            version.raw_content, filename=document.filename, source_type=document.source_type
        )
        known, public = await _privacy_values(session, tenant_id)
        safe_blocks = [
            TextBlock(
                redact_document_text(b.text, known, public),
                redact_document_text(b.section_title, known, public) if b.section_title else None,
                b.page,
            )
            for b in blocks
        ]
        chunks = chunk_blocks(safe_blocks)
        # Re-scan complete chunks as a second boundary and mask public contacts for egress.
        texts = [redact_document_text(c.content, known, public, for_embedding=True) for c in chunks]
        embeddings = await embedder.embed_documents(texts)
        if len(embeddings) != len(chunks) or any(
            len(v) != embedder.dimensions or any(not math.isfinite(x) for x in v)
            for v in embeddings
        ):
            raise ValueError("Resposta de embeddings inválida.")
        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            session.add(
                Chunk(
                    tenant_id=tenant_id,
                    document_id=document.id,
                    document_version_id=version.id,
                    chunk_index=index,
                    content=chunk.content,
                    token_count=len(tokenizer().encode(chunk.content, disallowed_special=())),
                    section_title=chunk.section_title,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    meta={
                        "suspicious_instructions": bool(INJECTION.search(chunk.content)),
                        "pii_redacted": "[REDACTED_" in chunk.content,
                    },
                    embedding=embedding,
                )
            )
        document.title = redact_document_text(document.title, known, public)
        document.source_url = (
            redact_document_text(document.source_url, known, public)
            if document.source_url
            else None
        )
        # Lock the document before advancing its pointer; an older slow job must not
        # supersede a newer version that has already completed.
        locked = await session.scalar(
            select(Document)
            .where(Document.id == document.id, Document.tenant_id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise ValueError("Documento não encontrado.")
        document = locked
        current_number = (
            await session.scalar(
                select(DocumentVersion.version).where(
                    DocumentVersion.tenant_id == tenant_id,
                    DocumentVersion.id == document.current_version_id,
                )
            )
            if document.current_version_id
            else None
        )
        if current_number is None or current_number <= version.version:
            document.current_version_id = version.id
        version.page_count = max((b.page or 0 for b in blocks), default=0) or None
        version.chunk_count = len(chunks)
        version.processed_at = utcnow()
        _transition(session, version, "completed")
        await session.commit()
    except (Exception, asyncio.CancelledError) as exc:
        await session.rollback()
        # Do not include exception text/traceback: both can contain uploaded PII.
        logger.error("document_ingestion_failed", extra={"error_category": type(exc).__name__})
        failed = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id, DocumentVersion.tenant_id == tenant_id
            )
        )
        if failed is None:
            raise ValueError("Versão não encontrada.") from None
        _transition(session, failed, "failed")
        failed.error_message = "Não foi possível processar o documento. Tente novamente."
        failed.chunk_count = 0
        failed.processed_at = utcnow()
        await session.commit()
        if isinstance(exc, asyncio.CancelledError):
            raise
