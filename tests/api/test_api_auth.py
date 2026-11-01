"""API-key authentication, scopes and audit of failed attempts."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update

from app.core.security import generate_api_key
from app.models import ApiKey, AuditEvent, Tenant
from app.services.attempt_limiter import InMemoryAttemptLimiter

pytestmark = pytest.mark.integration

URL = "/v1/handoffs"


async def _audit_for(session_factory, correlation_id: str) -> AuditEvent:
    async with session_factory() as s:
        return (
            await s.scalars(
                select(AuditEvent).where(
                    AuditEvent.correlation_id == correlation_id,
                    AuditEvent.event_type == "auth.failed",
                )
            )
        ).one()


async def test_missing_key_401_and_audited(client, session_factory):
    r = await client.get(URL, headers={"X-Request-ID": "a000000000000001"})
    assert r.status_code == 401
    assert r.json() == {
        "error": "unauthorized",
        "detail": "Invalid or missing API key",
        "correlation_id": "a000000000000001",
    }
    event = await _audit_for(session_factory, "a000000000000001")
    assert event.tenant_id is None
    assert event.data["reason"] == "missing"


@pytest.mark.parametrize("bad", ["garbage", "emp_short_x", "Bearer abc"])
async def test_malformed_key_401(client, bad):
    r = await client.get(URL, headers={"X-API-Key": bad})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or missing API key"


async def test_unknown_and_wrong_secret_401_audit_has_prefix_only(
    client, session_factory, two_tenants, make_key, fakes
):
    unknown, unknown_prefix = generate_api_key()
    r = await client.get(URL, headers={"X-API-Key": unknown, "X-Request-ID": "a000000000000002"})
    assert r.status_code == 401
    event = await _audit_for(session_factory, "a000000000000002")
    assert event.data == {"reason": "unknown", "key_prefix": unknown_prefix, "route": URL}

    # Audit rows are sampled to one per IP per minute: start a fresh window.
    fakes.limiter = InMemoryAttemptLimiter()
    real = await make_key(two_tenants[0])
    tampered = real[:-4] + ("AAAA" if not real.endswith("AAAA") else "BBBB")
    r = await client.get(URL, headers={"X-API-Key": tampered, "X-Request-ID": "a000000000000003"})
    assert r.status_code == 401
    event = await _audit_for(session_factory, "a000000000000003")
    assert event.data["reason"] == "bad_secret"
    assert tampered.split("_", 2)[2] not in str(event.data)
    assert real.split("_", 2)[2] not in str(event.data)


async def test_revoked_key_401(client, two_tenants, make_key):
    key = await make_key(two_tenants[0], revoked=True)
    r = await client.get(URL, headers={"X-API-Key": key})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or missing API key"


async def test_suspended_tenant_401(client, session_factory, two_tenants, make_key):
    key = await make_key(two_tenants[1])
    async with session_factory() as s:
        await s.execute(
            update(Tenant).where(Tenant.id == two_tenants[1]).values(status="suspended")
        )
        await s.commit()
    r = await client.get(URL, headers={"X-API-Key": key})
    assert r.status_code == 401


async def test_valid_key_200_and_last_used(client, session_factory, two_tenants, make_key):
    key = await make_key(two_tenants[0])
    r = await client.get(URL, headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json() == []
    async with session_factory() as s:
        row = await s.scalar(select(ApiKey).where(ApiKey.key_prefix == key.split("_")[1]))
        assert row is not None and row.last_used_at is not None


@pytest.mark.parametrize(
    ("method", "path", "needed"),
    [
        ("POST", "/v1/chat/messages", "chat"),
        ("GET", "/v1/handoffs", "handoffs"),
        ("GET", "/v1/knowledge/documents", "knowledge:read"),
        ("POST", "/v1/knowledge/documents/text", "knowledge:write"),
        ("GET", f"/v1/conversations/{uuid.uuid4()}", "conversations:read"),
    ],
)
async def test_scope_enforced_403(client, two_tenants, make_key, method, path, needed):
    others = [
        s
        for s in ("chat", "knowledge:read", "knowledge:write", "handoffs", "conversations:read")
        if s != needed
    ]
    key = await make_key(two_tenants[0], scopes=others)
    body = {"message": "oi", "title": "t", "content": "c"}
    r = await client.request(method, path, headers={"X-API-Key": key}, json=body)
    assert r.status_code == 403
    assert r.json()["error"] == "forbidden"
