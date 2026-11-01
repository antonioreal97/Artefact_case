"""Knowledge base management (scopes ``knowledge:write`` / ``knowledge:read``).

Uploads are size-capped while streaming (413), restricted to pdf/txt/md/docx by extension
AND content signature (415), stored as a ``pending`` version and handed to the ingestion
queue; the response is ``202 Accepted`` with an ``IngestionReceipt``. Poll the version
endpoint for ``completed``/``failed``. All queries filter by the authenticated tenant.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any

import pydantic
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select

from app.api.dependencies import (
    EnqueuerDep,
    Principal,
    SessionDep,
    SettingsDep,
    VersionCreatorDep,
    require_scope,
)
from app.core.logging import get_logger
from app.models import Document, DocumentVersion
from app.rag.types import IngestionReceipt
from app.schemas.api import (
    DocumentDetailOut,
    DocumentOut,
    DocumentTextRequest,
    DocumentVersionOut,
    ErrorResponse,
)

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
log = get_logger(__name__)

WriterDep = Annotated[Principal, Depends(require_scope("knowledge:write"))]
ReaderDep = Annotated[Principal, Depends(require_scope("knowledge:read"))]

ALLOWED_EXTENSIONS = ("pdf", "txt", "md", "docx")
_READ_CHUNK = 64 * 1024
_UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    413: {"model": ErrorResponse, "description": "File larger than MAX_UPLOAD_BYTES"},
    415: {"model": ErrorResponse, "description": "Not a pdf/txt/md/docx file"},
    422: {"model": ErrorResponse, "description": "Invalid form fields or document"},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found for this tenant"}
}


def _too_large() -> HTTPException:
    return HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="File too large")


def _unsupported() -> HTTPException:
    return HTTPException(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="Unsupported file type (allowed: pdf, txt, md, docx)",
    )


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    buffer = bytearray()
    while chunk := await file.read(_READ_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise _too_large()
    return bytes(buffer)


def _safe_filename(raw: str | None) -> str:
    name = PureWindowsPath(PurePosixPath(raw or "").name).name.strip()
    return name[:300]


def _detect_type(filename: str, data: bytes) -> str:
    """Extension must be allowed AND match the content signature (no renamed binaries)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise _unsupported()
    if ext == "pdf":
        ok = data.startswith(b"%PDF-")
    elif ext == "docx":
        ok = data.startswith(b"PK\x03\x04") and _is_docx(data)
    else:
        ok = b"\x00" not in data and _is_utf8(data)
    if not ok:
        raise _unsupported()
    return ext


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return "word/document.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


async def _ingest(
    session: SessionDep,
    create_version: VersionCreatorDep,
    enqueue: EnqueuerDep,
    principal: Principal,
    **kwargs: Any,
) -> IngestionReceipt:
    try:
        receipt = await create_version(session, tenant_id=principal.tenant_id, **kwargs)
    except pydantic.ValidationError:
        raise  # carries input values: never echoed; becomes a generic 500
    except ValueError as exc:
        # create_document_version raises ValueError with safe, user-facing messages.
        await session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)[:200]) from None
    if receipt.status == "pending":
        await _enqueue(enqueue, principal.tenant_id, receipt.version_id)
        receipt = await _refresh_receipt(session, principal.tenant_id, receipt)
    log.info(
        "knowledge.version_received",
        document_id=str(receipt.document_id),
        version_id=str(receipt.version_id),
        duplicate=receipt.duplicate,
        status=receipt.status,
    )
    return receipt


async def _enqueue(enqueue: EnqueuerDep, tenant_id: uuid.UUID, version_id: uuid.UUID) -> None:
    try:
        await enqueue(tenant_id, version_id)
    except Exception as exc:
        # The version stays pending/failed and can be retried via /reprocess.
        log.error("knowledge.enqueue_failed", version_id=str(version_id), exc_info=exc)


async def _refresh_receipt(
    session: SessionDep, tenant_id: uuid.UUID, receipt: IngestionReceipt
) -> IngestionReceipt:
    current = await session.scalar(
        select(DocumentVersion.ingestion_status)
        .where(DocumentVersion.id == receipt.version_id, DocumentVersion.tenant_id == tenant_id)
        .execution_options(populate_existing=True)
    )
    return receipt.model_copy(update={"status": current or receipt.status})


async def _get_document(session: SessionDep, tenant_id: uuid.UUID, doc_id: uuid.UUID) -> Document:
    document = await session.scalar(
        select(Document).where(Document.id == doc_id, Document.tenant_id == tenant_id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


async def _get_version(
    session: SessionDep, tenant_id: uuid.UUID, doc_id: uuid.UUID, version_id: uuid.UUID
) -> DocumentVersion:
    version = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == version_id,
            DocumentVersion.document_id == doc_id,
            DocumentVersion.tenant_id == tenant_id,
        )
    )
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Version not found")
    return version


@router.post(
    "/documents", response_model=IngestionReceipt, status_code=202, responses=_UPLOAD_ERRORS
)
async def upload_document(
    session: SessionDep,
    settings: SettingsDep,
    create_version: VersionCreatorDep,
    enqueue: EnqueuerDep,
    principal: WriterDep,
    file: Annotated[UploadFile, File(description="pdf, txt, md or docx")],
    title: Annotated[str, Form(min_length=1, max_length=300)],
    category: Annotated[str | None, Form(max_length=100)] = None,
    source_url: Annotated[str | None, Form(max_length=1000)] = None,
) -> IngestionReceipt:
    """Upload a document (multipart: `file`, `title`, optional `category`, `source_url`).

    `curl -H "X-API-Key: $KEY" -F file=@politicas.pdf -F title="Políticas"
    -F category=politicas /v1/knowledge/documents`

    Response 202: `{"document_id": "…", "version_id": "…", "version": 1,
    "status": "pending", "duplicate": false}`"""
    filename = _safe_filename(file.filename)
    data = await _read_limited(file, settings.max_upload_bytes)
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Empty file")
    source_type = _detect_type(filename, data)
    return await _ingest(
        session,
        create_version,
        enqueue,
        principal,
        title=title.strip(),
        data=data,
        filename=filename,
        source_type=source_type,
        category=category,
        source_url=source_url,
    )


@router.post(
    "/documents/text", response_model=IngestionReceipt, status_code=202, responses=_UPLOAD_ERRORS
)
async def create_text_document(
    body: DocumentTextRequest,
    session: SessionDep,
    settings: SettingsDep,
    create_version: VersionCreatorDep,
    enqueue: EnqueuerDep,
    principal: WriterDep,
) -> IngestionReceipt:
    """Create a document from plain text.

    Request: `{"title": "Política de trocas", "content": "Trocas em até 7 dias…",
    "category": "politicas"}` → 202 `IngestionReceipt` (same shape as file upload)."""
    if len(body.content) > settings.max_text_chars:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Content too large")
    return await _ingest(
        session,
        create_version,
        enqueue,
        principal,
        title=body.title,
        data=body.content.encode("utf-8"),
        filename=None,
        source_type="text",
        category=body.category,
        source_url=body.source_url,
    )


@router.post(
    "/documents/{document_id}/versions",
    response_model=IngestionReceipt,
    status_code=202,
    responses={**_UPLOAD_ERRORS, **_NOT_FOUND},
)
async def upload_document_version(
    document_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    create_version: VersionCreatorDep,
    enqueue: EnqueuerDep,
    principal: WriterDep,
    file: Annotated[UploadFile, File(description="pdf, txt, md or docx")],
) -> IngestionReceipt:
    """Upload a new version of an existing document (multipart `file`). Identical content
    returns the existing version with `"duplicate": true`."""
    document = await _get_document(session, principal.tenant_id, document_id)
    filename = _safe_filename(file.filename)
    data = await _read_limited(file, settings.max_upload_bytes)
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Empty file")
    source_type = _detect_type(filename, data)
    return await _ingest(
        session,
        create_version,
        enqueue,
        principal,
        title=document.title,
        data=data,
        filename=filename,
        source_type=source_type,
        category=document.category,
        source_url=document.source_url,
        document_id=document.id,
    )


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    session: SessionDep,
    principal: ReaderDep,
    category: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentOut]:
    """Newest first; optional `?category=`."""
    query = select(Document).where(Document.tenant_id == principal.tenant_id)
    if category is not None:
        query = query.where(Document.category == category)
    rows = await session.scalars(
        query.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    )
    return [DocumentOut.model_validate(d) for d in rows]


@router.get("/documents/{document_id}", response_model=DocumentDetailOut, responses=_NOT_FOUND)
async def get_document(
    document_id: uuid.UUID, session: SessionDep, principal: ReaderDep
) -> DocumentDetailOut:
    """Document metadata plus all versions (newest first)."""
    document = await _get_document(session, principal.tenant_id, document_id)
    versions = await session.scalars(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.tenant_id == principal.tenant_id,
        )
        .order_by(DocumentVersion.version.desc())
    )
    detail = DocumentDetailOut.model_validate(document)
    detail.versions = [DocumentVersionOut.model_validate(v) for v in versions]
    return detail


@router.get(
    "/documents/{document_id}/versions/{version_id}",
    response_model=DocumentVersionOut,
    responses=_NOT_FOUND,
)
async def get_document_version(
    document_id: uuid.UUID, version_id: uuid.UUID, session: SessionDep, principal: ReaderDep
) -> DocumentVersionOut:
    """Ingestion status. Example: `{"id": "…", "document_id": "…", "version": 2,
    "status": "failed", "error_message": "Não foi possível extrair texto do PDF.",
    "chunk_count": null, …}`"""
    version = await _get_version(session, principal.tenant_id, document_id, version_id)
    return DocumentVersionOut.model_validate(version)


@router.post(
    "/documents/{document_id}/versions/{version_id}/reprocess",
    response_model=IngestionReceipt,
    status_code=202,
    responses={**_NOT_FOUND, 409: {"model": ErrorResponse, "description": "Already processing"}},
)
async def reprocess_document_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    session: SessionDep,
    enqueue: EnqueuerDep,
    principal: WriterDep,
) -> IngestionReceipt:
    """Reset a version to `pending` and enqueue ingestion again (e.g. after a failure)."""
    version = await _get_version(session, principal.tenant_id, document_id, version_id)
    if version.ingestion_status == "processing":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Version is being processed")
    version.ingestion_status = "pending"
    version.error_message = None
    await session.commit()
    receipt = IngestionReceipt(
        document_id=version.document_id,
        version_id=version.id,
        version=version.version,
        status="pending",
        duplicate=False,
    )
    await _enqueue(enqueue, principal.tenant_id, version.id)
    return await _refresh_receipt(session, principal.tenant_id, receipt)
