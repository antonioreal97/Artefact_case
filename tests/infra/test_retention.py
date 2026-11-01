"""Review M-10: retention purge deletes expired restricted records per tenant only."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import (
    AuditEvent,
    Conversation,
    Document,
    DocumentVersion,
    HandoffRequest,
    Message,
    MessageSource,
    ToolExecution,
)
from app.models.base import utcnow
from app.services.ingestion_jobs import WorkerSettings, purge_retention
from app.services.retention import purge_expired

pytestmark = pytest.mark.integration

OLD = timedelta(days=120)


async def _conversation(s: AsyncSession, tenant: uuid.UUID, *, age: timedelta) -> uuid.UUID:
    conv = Conversation(tenant_id=tenant, channel="api")
    s.add(conv)
    await s.flush()
    msg = Message(
        tenant_id=tenant,
        conversation_id=conv.id,
        role="user",
        content="meu email é cliente@example.test",
        content_redacted="meu email é [[EMAIL_1]]",
        meta={},
    )
    s.add(msg)
    await s.flush()
    s.add_all(
        [
            MessageSource(
                tenant_id=tenant, message_id=msg.id, label="S1", title="t", score=1.0, quote="q"
            ),
            ToolExecution(
                tenant_id=tenant,
                conversation_id=conv.id,
                tool_name="lookup_order",
                status="error",
            ),
            HandoffRequest(tenant_id=tenant, conversation_id=conv.id, reason="tool_failure"),
        ]
    )
    await s.flush()
    stamp = utcnow() - age
    await s.execute(
        update(Conversation)
        .where(Conversation.id == conv.id)
        .values(updated_at=stamp, created_at=stamp)
    )
    return conv.id


async def _document(s: AsyncSession, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    doc = Document(tenant_id=tenant, title="Política", source_type="md")
    s.add(doc)
    await s.flush()
    v1, v2 = (
        DocumentVersion(
            tenant_id=tenant,
            document_id=doc.id,
            version=n,
            content_hash=uuid.uuid4().hex,
            mime_type="text/markdown",
            byte_size=5,
            raw_content=f"v{n}  ".encode(),
            ingestion_status="completed",
        )
        for n in (1, 2)
    )
    s.add_all([v1, v2])
    await s.flush()
    doc.current_version_id = v2.id
    return v1.id, v2.id


async def _count(s: AsyncSession, model: type, *conditions: object) -> int:
    return int(await s.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)


async def test_purge_expired_per_tenant(
    session_factory: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_tenants
    async with session_factory() as s:
        old_a = await _conversation(s, a, age=OLD)
        fresh_a = await _conversation(s, a, age=timedelta(days=1))
        old_b = await _conversation(s, b, age=OLD)
        s.add_all(
            [
                AuditEvent(tenant_id=a, event_type="old", created_at=utcnow() - OLD),
                AuditEvent(tenant_id=a, event_type="fresh"),
                AuditEvent(tenant_id=b, event_type="old", created_at=utcnow() - OLD),
            ]
        )
        old_v1, current_v2 = await _document(s, a)
        await s.commit()

    settings = Settings(app_env="test", retention_days=90)
    report = await purge_expired(session_factory, settings)
    assert report.conversations >= 2 and report.audit_events >= 2
    assert report.per_tenant[str(a)]["conversations"] == 1
    assert report.per_tenant[str(a)]["raw_contents_cleared"] == 1

    async with session_factory() as s:
        assert await _count(s, Conversation, Conversation.id.in_([old_a, old_b])) == 0
        assert await _count(s, Conversation, Conversation.id == fresh_a) == 1
        # Cascades: nothing of the expired conversations is left.
        for model in (Message, ToolExecution, HandoffRequest):
            assert await _count(s, model, model.conversation_id.in_([old_a, old_b])) == 0
            assert await _count(s, model, model.conversation_id == fresh_a) == 1
        assert await _count(s, MessageSource, MessageSource.tenant_id == b) == 0
        assert await _count(s, MessageSource, MessageSource.tenant_id == a) == 1
        stmt = select(AuditEvent.event_type).where(AuditEvent.tenant_id == a)
        events = set(await s.scalars(stmt))
        assert "old" not in events and "fresh" in events
        v1 = await s.get(DocumentVersion, old_v1)
        v2 = await s.get(DocumentVersion, current_v2)
        assert v1 is not None and v1.raw_content == b"" and v1.byte_size == 5
        assert v2 is not None and v2.raw_content == b"v2  "  # current version untouched


async def test_now_parameter_and_tenant_isolation(
    session_factory: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_tenants
    async with session_factory() as s:
        conv_a = await _conversation(s, a, age=timedelta(days=10))
        conv_b = await _conversation(s, b, age=timedelta(days=10))
        await s.commit()
    settings = Settings(app_env="test", retention_days=30)
    await purge_expired(session_factory, settings)  # nothing expired yet
    async with session_factory() as s:
        assert await _count(s, Conversation, Conversation.id.in_([conv_a, conv_b])) == 2
    await purge_expired(session_factory, settings, now=utcnow() + timedelta(days=25))
    async with session_factory() as s:
        assert await _count(s, Conversation, Conversation.id.in_([conv_a, conv_b])) == 0
    with pytest.raises(ValueError):
        await purge_expired(session_factory, Settings(app_env="test", retention_days=0))


async def test_worker_schedules_daily_purge(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    names = [job.coroutine.__name__ for job in WorkerSettings.cron_jobs]
    assert names == ["purge_retention"]
    result = await purge_retention({"session_factory": session_factory})
    assert set(result) >= {"tenants", "conversations", "audit_events"}
