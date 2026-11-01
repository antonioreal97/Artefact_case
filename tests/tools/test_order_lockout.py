"""Review H-1 / L-4 / L-6 regressions: the order-lookup lockout survives new conversations,
locks per target and per actor, fails closed when the limiter is down, audits without PII
and the backend reads order + customer in one query."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import event, select

from app.integrations.local_commerce import LocalCommerceBackend
from app.models import AuditEvent, Conversation
from app.services.attempt_limiter import InMemoryAttemptLimiter
from app.tools.orders import (
    AUDIT_EVENT_TYPE,
    MSG_LOCKED,
    MSG_NOT_AUTHORIZED,
    lookup_order,
    order_failure_key,
)
from emporio_agent.privacy import find_restricted_data
from tests.tools.conftest import A_EMAIL, A_PHONE, make_ctx

pytestmark = pytest.mark.integration


async def _new_conversation(session_factory, tenant_id):
    conv = Conversation(tenant_id=tenant_id, channel="api")
    async with session_factory() as s:
        s.add(conv)
        await s.commit()
    return conv.id


async def _attempt(ctx, order: str, contact: str, kind: str = "EMAIL"):
    return await lookup_order(
        ctx, ctx.vault.tokenize("ORDER", order), ctx.vault.tokenize(kind, contact)
    )


async def test_lockout_survives_new_conversation(session_factory, seed):
    """Review repro: 3 wrong e-mails in conv1, then a NEW conversation with the right e-mail
    used to return the tracking code. Now the actor stays locked across conversations."""
    limiter = InMemoryAttemptLimiter()
    actor = "api-key:attacker"
    conv1 = make_ctx(
        session_factory, seed.tenant_a, seed.conversation_a, limiter=limiter, actor_ref=actor
    )
    for guess in ("a@x.com", "b@x.com", "c@x.com"):
        res = await _attempt(conv1, "101", guess)
        assert res.error.code == "not_authorized" and res.error.message == MSG_NOT_AUTHORIZED
    locked = await _attempt(conv1, "101", A_EMAIL)
    assert locked.error.message == MSG_LOCKED

    conv2_id = await _new_conversation(session_factory, seed.tenant_a)
    conv2 = make_ctx(session_factory, seed.tenant_a, conv2_id, limiter=limiter, actor_ref=actor)
    res = await _attempt(conv2, "101", A_EMAIL)
    assert not res.ok and res.error.code == "not_authorized"
    assert res.error.message == MSG_LOCKED
    assert res.data is None  # no tracking token was minted
    assert conv2.tool_calls[-1].result_summary["locked"] is True


async def test_per_target_lockout_across_actors(session_factory, seed):
    """Different actors guessing the SAME order lock that order for everybody."""
    limiter = InMemoryAttemptLimiter()
    for i in range(3):
        conv_id = await _new_conversation(session_factory, seed.tenant_a)
        ctx = make_ctx(
            session_factory, seed.tenant_a, conv_id, limiter=limiter, actor_ref=f"actor-{i}"
        )
        res = await _attempt(ctx, "101", f"guess{i}@x.com")
        assert res.error.message == MSG_NOT_AUTHORIZED
    fresh = make_ctx(
        session_factory, seed.tenant_a, seed.conversation_a, limiter=limiter, actor_ref="new"
    )
    res = await _attempt(fresh, "101", A_EMAIL)
    assert res.error.message == MSG_LOCKED
    # Another order of the same tenant is not affected for a fresh actor.
    other = await _attempt(fresh, "102", A_PHONE, kind="PHONE")
    assert other.ok


async def test_actor_defaults_to_conversation_and_tenants_are_separate(session_factory, seed):
    limiter = InMemoryAttemptLimiter()
    ctx_a = make_ctx(session_factory, seed.tenant_a, seed.conversation_a, limiter=limiter)
    for i in range(3):
        await _attempt(ctx_a, "101", f"g{i}@x.com")
    assert (await _attempt(ctx_a, "102", A_EMAIL)).error.message == MSG_LOCKED
    # Tenant B's order 101 shares the external id but not the counter.
    ctx_b = make_ctx(session_factory, seed.tenant_b, seed.conversation_b, limiter=limiter)
    assert await limiter.failures(order_failure_key(ctx_b, 101)) == 0
    res = await _attempt(ctx_b, "101", "cliente@lojasintetica.test")
    assert res.ok


class _BrokenLimiter:
    async def failures(self, key: str) -> int:
        raise ConnectionError("redis down")

    async def record_failure(self, key: str, window_seconds: int) -> int:
        raise ConnectionError("redis down")

    async def hit(self, key: str, window_seconds: int) -> int:
        raise ConnectionError("redis down")


class _WriteBrokenLimiter(InMemoryAttemptLimiter):
    async def record_failure(self, key: str, window_seconds: int) -> int:
        raise ConnectionError("redis down")


@pytest.mark.parametrize("limiter_cls", [_BrokenLimiter, _WriteBrokenLimiter])
async def test_limiter_errors_fail_closed(session_factory, seed, limiter_cls):
    ctx = make_ctx(session_factory, seed.tenant_a, seed.conversation_a, limiter=limiter_cls())
    # Even the CORRECT contact is refused when the counter cannot be read.
    contact = A_EMAIL if limiter_cls is _BrokenLimiter else "wrong@x.com"
    res = await _attempt(ctx, "101", contact)
    assert not res.ok and res.error.code == "unavailable"
    assert ctx.tool_calls[-1].result_summary["limiter_unavailable"] is True


async def test_failed_verification_audit_event_has_no_pii(session_factory, seed):
    ctx = make_ctx(
        session_factory,
        seed.tenant_a,
        seed.conversation_a,
        actor_ref="opaque-ref-123",
    )
    await _attempt(ctx, "101", "(11) 90000-0000", kind="PHONE")
    async with session_factory() as s:
        rows = (
            await s.scalars(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == seed.tenant_a,
                    AuditEvent.event_type == AUDIT_EVENT_TYPE,
                    AuditEvent.correlation_id == ctx.correlation_id,
                )
            )
        ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.data["contact_kind"] == "phone"
    assert row.data["order_failures"] == 1 and row.data["actor_failures"] == 1
    assert len(row.data["actor_hash"]) == 32 and "opaque-ref-123" not in row.data["actor_hash"]
    dumped = json.dumps(
        {"data": row.data, "resource_id": row.resource_id, "actor": row.actor}
    ).casefold()
    for raw in ("101", "90000", "opaque-ref-123", A_EMAIL.casefold()):
        assert raw not in dumped
    assert find_restricted_data(row.data) == ()


async def test_get_order_runs_a_single_query(session_factory, seed, db_engine):
    """Review L-6: same number of statements for an existing and a missing order."""
    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        if "commerce_orders" in statement:
            statements.append(statement)

    event.listen(db_engine.sync_engine, "before_cursor_execute", count)
    try:
        backend = LocalCommerceBackend(session_factory, seed.tenant_a)
        found = await backend.get_order(101)
        hits_existing = len(statements)
        missing = await backend.get_order(99999)
        hits_missing = len(statements) - hits_existing
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", count)
    assert found is not None and found.customer_email == A_EMAIL
    assert missing is None
    assert hits_existing == hits_missing == 1
    assert "commerce_customers" in statements[0]  # order + customer joined
