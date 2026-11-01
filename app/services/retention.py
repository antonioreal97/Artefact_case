"""Retention purge (review M-10; SECURITY.md: bounded retention for restricted records).

``purge_expired`` runs per tenant, one transaction each, every statement filtered by
``tenant_id``:

* conversations whose ``updated_at`` is older than ``retention_days`` are deleted; the
  database cascades to messages (raw text), message sources, tool executions and handoff
  requests (``ON DELETE CASCADE`` in migration 0001);
* tool executions without a conversation and audit events older than the cutoff;
* ``document_versions.raw_content`` of SUPERSEDED versions (an older version number than
  the document's current version) is emptied - the bytes are only kept for reprocessing,
  and chunks of the current version are unaffected.

Pre-authentication audit events (``tenant_id IS NULL``) older than the cutoff are purged in
a final transaction. Scheduled daily by the arq worker (``ingestion_jobs.WorkerSettings``)
and runnable by hand: ``uv run python -m scripts.purge_retention``. Only counts are logged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import CursorResult, delete, exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import AuditEvent, Conversation, Document, DocumentVersion, Tenant, ToolExecution
from app.models.base import utcnow

log = get_logger(__name__)

# The purge may touch many rows; the API's short statement timeout does not apply here.
_PURGE_STATEMENT_TIMEOUT = "300s"


@dataclass
class PurgeReport:
    cutoff: datetime
    tenants: int = 0
    conversations: int = 0
    tool_executions: int = 0
    audit_events: int = 0
    raw_contents_cleared: int = 0
    per_tenant: dict[str, dict[str, int]] = field(default_factory=dict)


def _count(result: object) -> int:
    return int(result.rowcount) if isinstance(result, CursorResult) else 0


async def _purge_tenant(
    session: AsyncSession, tenant_id: uuid.UUID, cutoff: datetime
) -> dict[str, int]:
    await session.execute(text(f"SET LOCAL statement_timeout = '{_PURGE_STATEMENT_TIMEOUT}'"))
    conversations = await session.execute(
        delete(Conversation).where(
            Conversation.tenant_id == tenant_id, Conversation.updated_at < cutoff
        )
    )
    orphan_tools = await session.execute(
        delete(ToolExecution).where(
            ToolExecution.tenant_id == tenant_id,
            ToolExecution.conversation_id.is_(None),
            ToolExecution.created_at < cutoff,
        )
    )
    audits = await session.execute(
        delete(AuditEvent).where(AuditEvent.tenant_id == tenant_id, AuditEvent.created_at < cutoff)
    )
    current = aliased(DocumentVersion)
    superseded = exists().where(
        Document.id == DocumentVersion.document_id,
        Document.tenant_id == tenant_id,
        current.id == Document.current_version_id,
        current.tenant_id == tenant_id,
        current.version > DocumentVersion.version,
    )
    cleared = await session.execute(
        update(DocumentVersion)
        .where(
            DocumentVersion.tenant_id == tenant_id,
            DocumentVersion.ingestion_status.in_(("completed", "failed")),
            func.octet_length(DocumentVersion.raw_content) > 0,
            superseded,
        )
        .values(raw_content=b"")
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return {
        "conversations": _count(conversations),
        "tool_executions": _count(orphan_tools),
        "audit_events": _count(audits),
        "raw_contents_cleared": _count(cleared),
    }


async def purge_expired(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    now: datetime | None = None,
) -> PurgeReport:
    if settings.retention_days <= 0:
        raise ValueError("retention_days must be positive")
    cutoff = (now or utcnow()) - timedelta(days=settings.retention_days)
    report = PurgeReport(cutoff=cutoff)
    async with session_factory() as session:
        tenant_ids = list(await session.scalars(select(Tenant.id).order_by(Tenant.id)))
    for tenant_id in tenant_ids:
        async with session_factory() as session:
            counts = await _purge_tenant(session, tenant_id, cutoff)
        report.tenants += 1
        report.conversations += counts["conversations"]
        report.tool_executions += counts["tool_executions"]
        report.audit_events += counts["audit_events"]
        report.raw_contents_cleared += counts["raw_contents_cleared"]
        report.per_tenant[str(tenant_id)] = counts
    async with session_factory() as session:
        await session.execute(text(f"SET LOCAL statement_timeout = '{_PURGE_STATEMENT_TIMEOUT}'"))
        pre_auth = await session.execute(
            delete(AuditEvent).where(AuditEvent.tenant_id.is_(None), AuditEvent.created_at < cutoff)
        )
        await session.commit()
    report.audit_events += _count(pre_auth)
    log.info(
        "retention.purged",
        retention_days=settings.retention_days,
        tenants=report.tenants,
        conversations=report.conversations,
        tool_executions=report.tool_executions,
        audit_events=report.audit_events,
        raw_contents_cleared=report.raw_contents_cleared,
    )
    return report


__all__ = ["PurgeReport", "purge_expired"]
