"""Tenant-scoped, versioned ingestion with local PII minimization.

PII is irreversibly replaced with redact_for_log-style [REDACTED_*] markers
before chunking, storage and embedding (scanning before splitting prevents boundary
bypasses). Public merchant contacts are preserved in stored chunks (with their original
formatting) but replaced with [PUBLIC_CONTACT] for embedding, whose final egress guard has
no allowlist. Only error categories are logged: parser/provider exceptions can contain raw
PII.

Redaction must not destroy policy text (review M-6): contextual labels redact only a value
that looks personal (a capitalized full name after ``nome|cliente|destinatário:``, a street
address with a number, a CPF), never ``entrega:`` and never the rest of the line. Known
tenant values (customer names/e-mails/phones, tracking codes, long order notes) are matched
with word boundaries; numeric order ids and short notes are not used (they are not PII
inside a policy and would erase prices or section numbers).

PDF/DOCX parsing is CPU-bound and runs in a worker thread (review M-9). A version stuck in
``processing`` longer than ``STALE_PROCESSING_AFTER`` (e.g. its worker died) may be
reprocessed (review L-12): ``processed_at`` is stamped when processing starts and again
when it ends, so it always holds the time of the last status change.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta

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
_CAP_WORD = r"[A-ZÀ-Ý][a-zà-ÿ]+"
_NAME = rf"{_CAP_WORD}(?:[ \t]+(?:(?:d[aeo]s?|e)[ \t]+)?{_CAP_WORD})+"  # one line only
_STREET = r"(?i:rua|r\.|avenida|av\.|alameda|al\.|travessa|tv\.|rodovia|estrada|pra[çc]a)"
_ADDRESS = (
    rf"\b{_STREET}\s+[^\n,;:]{{1,60}}?,?\s*(?:(?i:n[º°o.]?)\s*)?\d{{1,5}}\b"
    r"(?:\s*[,-]\s*(?i:apto|apartamento|ap\.|bloco|bl\.|casa|sala|conj\.?)\s*[\w-]{1,8})*"
)
_CPF = (
    r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)"
    r"|(?i:\bcpf\b)[^\d\n]{0,12}\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}(?!\d)"
)
# Personal-looking values only; the label (if any) is kept, the rest of the line is kept.
CONTEXTUAL_PII = re.compile(
    rf"(?P<label>\b(?i:nome(?:\s+do\s+cliente)?|cliente|destinat[aá]rio)\s*:\s*)(?P<name>{_NAME})"
    rf"|(?P<address>{_ADDRESS})"
    rf"|(?P<cpf>{_CPF})"
)
PERSONAL_DATA_MARKER = "[REDACTED_PERSONAL_DATA]"
KNOWN_VALUE_MARKER = "[REDACTED_KNOWN_VALUE]"
_PUBLIC_MARKER_PREFIX = "PUBLICCONTACTTOKEN"
MIN_NOTE_CHARS = 12
STALE_PROCESSING_AFTER = timedelta(minutes=15)


def _contextual_sub(match: re.Match[str]) -> str:
    if match.group("name"):
        return match.group("label") + PERSONAL_DATA_MARKER
    return PERSONAL_DATA_MARKER


def _known_pattern(known: Iterable[str]) -> re.Pattern[str] | None:
    """Case-insensitive, word-bounded alternation of known values (longest first)."""
    values = sorted({v.strip() for v in known if v and len(v.strip()) >= 3}, key=len, reverse=True)
    if not values:
        return None
    return re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(v) for v in values) + r")(?!\w)", re.IGNORECASE
    )


def _letters(index: int) -> str:
    out = ""
    while True:
        index, rest = divmod(index, 26)
        out = chr(65 + rest) + out
        if index == 0:
            return out
        index -= 1


def known_privacy_values(
    customers: Iterable[tuple[str, str, str]], orders: Iterable[tuple[str | None, str | None]]
) -> list[str]:
    """Known restricted values: customer (name, email, phone) and order (tracking, notes).
    Numeric order ids are deliberately excluded; notes only when long enough to be specific."""
    known: list[str] = []
    for name, email, phone in customers:
        known.extend([name, email, phone])
    for tracking_code, notes in orders:
        if tracking_code:
            known.append(tracking_code)
        if notes and len(notes.strip()) >= MIN_NOTE_CHARS:
            known.append(notes)
    return known


def is_stale_processing(version: DocumentVersion, now: datetime | None = None) -> bool:
    """True when a ``processing`` version has not changed state for STALE_PROCESSING_AFTER."""
    if version.ingestion_status != "processing":
        return False
    last_change = version.processed_at or version.created_at
    if last_change is None:
        return True
    return (now or utcnow()) - last_change > STALE_PROCESSING_AFTER


def _strip_nul(value: str | None) -> str | None:
    return value.replace("\x00", "") if value is not None else None


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
    # Defensive: PostgreSQL text cannot store NUL (the API already rejects it with 422).
    title = _strip_nul(title) or ""
    filename, category, source_url = (_strip_nul(v) for v in (filename, category, source_url))
    # Full validation precedes persistence, including decoded-size/encryption checks.
    # CPU-bound parsing runs off the event loop.
    await asyncio.to_thread(extract_document, data, filename=filename, source_type=source_type)
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
    customers = await session.execute(
        select(CommerceCustomer.name, CommerceCustomer.email, CommerceCustomer.phone).where(
            CommerceCustomer.tenant_id == tenant_id
        )
    )
    orders = await session.execute(
        select(CommerceOrder.tracking_code, CommerceOrder.notes).where(
            CommerceOrder.tenant_id == tenant_id
        )
    )
    return (
        known_privacy_values(
            [(name, email, phone) for name, email, phone in customers],
            [(tracking, notes) for tracking, notes in orders],
        ),
        public,
    )


def redact_document_text(
    text: str, known: list[str], public: list[str], *, for_embedding: bool = False
) -> str:
    if _PUBLIC_MARKER_PREFIX in text:
        raise ValueError("Marcador reservado no documento.")
    # Protect exact, administrator-approved public values, without logging them. Each
    # occurrence keeps its ORIGINAL text (formatting variants in the allowlist must not
    # rewrite the document).
    originals: dict[str, str] = {}

    def protect(match: re.Match[str]) -> str:
        marker = f"{_PUBLIC_MARKER_PREFIX}{_letters(len(originals))}END"
        originals[marker] = match.group(0)
        return marker

    for value in sorted((v for v in public if v and v.strip()), key=len, reverse=True):
        text = re.sub(
            r"\s*".join(re.escape(char) for char in value if not char.isspace()),
            protect,
            text,
            flags=re.IGNORECASE,
        )
    known_re = _known_pattern(known)
    if find_restricted_data(text):
        text = str(redact_for_log(text))
    if known_re is not None:
        text = known_re.sub(KNOWN_VALUE_MARKER, text)
    text = CONTEXTUAL_PII.sub(_contextual_sub, text)
    if find_restricted_data(text) or (known_re is not None and known_re.search(text)):
        raise ValueError("Documento não passou pela proteção de privacidade.")
    for marker, original in originals.items():
        text = text.replace(marker, "[PUBLIC_CONTACT]" if for_embedding else original)
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
    if version.ingestion_status == "processing" and not is_stale_processing(version):
        await session.commit()
        return
    _transition(session, version, "processing")
    version.error_message = None
    version.processed_at = utcnow()  # start of processing (stale detection, L-12)
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
        blocks = await asyncio.to_thread(
            extract_document,
            version.raw_content,
            filename=document.filename,
            source_type=document.source_type,
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
