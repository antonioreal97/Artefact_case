from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import AuditEvent, Conversation, HandoffRequest
from app.tools.handoff import request_human_handoff

pytestmark = pytest.mark.integration


async def test_handoff_creates_request_status_and_audit(ctx_a, seed, session_factory):
    summary = "Cliente lucas.mendes@jmail.com, tel (67) 99812-3456, reclama do pedido 101."
    res = await request_human_handoff(ctx_a, "sensitive_topic", summary)
    assert res.ok, res.error
    assert res.data.reason == "sensitive_topic" and res.data.status == "open"
    assert ctx_a.handoff_id is not None and ctx_a.handoff_reason == "sensitive_topic"

    async with session_factory() as s:
        handoff = await s.get(HandoffRequest, ctx_a.handoff_id)
        conv = await s.get(Conversation, seed.conversation_a)
        audit = (
            await s.scalars(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == seed.tenant_a,
                    AuditEvent.resource_id == str(ctx_a.handoff_id),
                )
            )
        ).one()
        other_conv = await s.get(Conversation, seed.conversation_b)
    assert handoff.tenant_id == seed.tenant_a and handoff.conversation_id == seed.conversation_a
    assert handoff.status == "open"
    for raw in ("lucas.mendes@jmail.com", "99812-3456", "pedido 101"):
        assert raw not in handoff.summary
    assert "[REDACTED_EMAIL]" in handoff.summary
    assert conv.status == "handoff"
    assert other_conv.status == "open"
    assert audit.event_type == "handoff.created"
    assert audit.correlation_id == ctx_a.correlation_id
    assert audit.data["reason"] == "sensitive_topic"

    record = ctx_a.tool_calls[-1]
    assert record.status == "success"
    assert record.arguments_redacted == {"reason": "sensitive_topic", "summary_chars": len(summary)}


async def test_handoff_is_idempotent_within_turn(ctx_a, seed, session_factory):
    first = await request_human_handoff(ctx_a, "customer_request", "quer falar com humano")
    first_id = ctx_a.handoff_id
    second = await request_human_handoff(ctx_a, "tool_failure", "outra vez")
    assert first.model_dump() == second.model_dump()
    assert ctx_a.handoff_id == first_id and ctx_a.handoff_reason == "customer_request"
    async with session_factory() as s:
        count = await s.scalar(
            select(func.count())
            .select_from(HandoffRequest)
            .where(HandoffRequest.conversation_id == seed.conversation_a)
        )
    assert count == 1


async def test_handoff_rejects_unknown_reason(ctx_a, seed, session_factory):
    res = await request_human_handoff(ctx_a, "because", "x")
    assert not res.ok and res.error.code == "invalid_input"
    assert ctx_a.handoff_id is None
    async with session_factory() as s:
        conv = await s.get(Conversation, seed.conversation_a)
    assert conv.status == "open"


async def test_handoff_cannot_touch_other_tenant_conversation(ctx_factory, seed, session_factory):
    # Tenant B context pointed at tenant A's conversation id.
    ctx = ctx_factory(tenant="b")
    ctx.conversation_id = seed.conversation_a
    res = await request_human_handoff(ctx, "customer_request", "x")
    assert not res.ok and res.error.code == "not_found"
    async with session_factory() as s:
        conv = await s.get(Conversation, seed.conversation_a)
        count = await s.scalar(
            select(func.count())
            .select_from(HandoffRequest)
            .where(HandoffRequest.conversation_id == seed.conversation_a)
        )
    assert conv.status == "open" and count == 0
