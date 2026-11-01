"""Security-review regressions for the HTTP layer: M-8 (rate limits + sampled auth audit),
L-7 (WhatsApp dedupe states), L-9 (X-Request-ID), L-11 (docs routes), L-12a (reprocess)
and the limiter wiring into ChatService (H-1)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update

from app.api import dependencies as deps
from app.core.config import Settings
from app.main import create_app
from app.models import AuditEvent, Document, DocumentVersion, TenantChannel
from app.models.base import utcnow
from app.services.attempt_limiter import InMemoryAttemptLimiter

pytestmark = pytest.mark.integration

CHAT = "/v1/chat/messages"
WA = "/v1/webhooks/whatsapp"


class BrokenLimiter:
    async def failures(self, key: str) -> int:
        raise ConnectionError("redis down")

    async def record_failure(self, key: str, window_seconds: int) -> int:
        raise ConnectionError("redis down")

    async def hit(self, key: str, window_seconds: int) -> int:
        raise ConnectionError("redis down")


# --- M-8: chat rate limit per API key ---------------------------------------------------


async def test_chat_rate_limited_per_api_key(client, two_tenants, make_key, api_settings, fakes):
    api_settings.chat_rate_limit_per_minute = 2
    key_a = await make_key(two_tenants[0], scopes=["chat"])
    key_b = await make_key(two_tenants[0], scopes=["chat"])
    for _ in range(2):
        r = await client.post(CHAT, headers={"X-API-Key": key_a}, json={"message": "oi"})
        assert r.status_code == 200
    r = await client.post(CHAT, headers={"X-API-Key": key_a}, json={"message": "oi"})
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    assert r.json()["error"] == "too_many_requests"
    assert len(fakes.chat.calls) == 2  # the model is not called when limited
    # Another key of the same tenant has its own budget.
    r = await client.post(CHAT, headers={"X-API-Key": key_b}, json={"message": "oi"})
    assert r.status_code == 200


async def test_rate_limits_fail_open_when_limiter_down(client, two_tenants, make_key, fakes):
    fakes.limiter = BrokenLimiter()  # type: ignore[assignment]
    key = await make_key(two_tenants[0], scopes=["chat"])
    r = await client.post(CHAT, headers={"X-API-Key": key}, json={"message": "oi"})
    assert r.status_code == 200
    r = await client.get("/v1/handoffs", headers={"X-API-Key": "garbage"})
    assert r.status_code == 401


# --- M-8: auth failures per client IP + sampled audit -----------------------------------


async def _auth_failed_rows(session_factory, prefix: str) -> int:
    async with session_factory() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == "auth.failed",
                    AuditEvent.correlation_id.like(f"{prefix}%"),
                )
            )
            or 0
        )


async def test_auth_failures_throttled_per_ip_and_audited_once(
    app, session_factory, two_tenants, make_key, api_settings
):
    api_settings.auth_failure_limit_per_minute = 3
    good = await make_key(two_tenants[0])
    prefix = uuid.uuid4().hex[:8]

    async def call(ip: str, key: str, n: int) -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=(ip, 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            rid = f"{prefix}{n:08x}"
            return await c.get("/v1/handoffs", headers={"X-API-Key": key, "X-Request-ID": rid})

    statuses = [(await call("10.0.0.1", "garbage", n)).status_code for n in range(5)]
    assert statuses == [401, 401, 401, 429, 429]
    blocked = await call("10.0.0.1", good, 9)
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "60"
    # Other IPs are unaffected.
    assert (await call("10.0.0.2", good, 10)).status_code == 200
    # One audit row per IP per window, not one per failure (DB growth / DoS).
    assert await _auth_failed_rows(session_factory, prefix) == 1


async def test_auth_audit_written_when_limiter_down(app, session_factory, fakes):
    fakes.limiter = BrokenLimiter()  # type: ignore[assignment]
    prefix = uuid.uuid4().hex[:8]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        for n in range(2):
            r = await c.get("/v1/handoffs", headers={"X-Request-ID": f"{prefix}{n:08x}"})
            assert r.status_code == 401
    assert await _auth_failed_rows(session_factory, prefix) == 2


# --- WhatsApp: per-user rate limit, L-7 dedupe states, L-8 ref ---------------------------


@pytest.fixture
async def wa_channel(session_factory, two_tenants) -> str:
    phone_number_id = f"pn-{uuid.uuid4().hex[:10]}"
    async with session_factory() as s:
        s.add(
            TenantChannel(tenant_id=two_tenants[0], channel="whatsapp", external_id=phone_number_id)
        )
        await s.commit()
    return phone_number_id


def _wa_body(channel: str, message_id: str, wa_id: str = "5567999990000") -> bytes:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": channel},
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": wa_id,
                                    "type": "text",
                                    "text": {"body": "Vocês abrem domingo?"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    return json.dumps(payload).encode()


def _signed(body: bytes) -> dict[str, str]:
    sig = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"}


async def _post_wa(client, body: bytes) -> dict[str, Any]:
    r = await client.post(WA, content=body, headers=_signed(body))
    assert r.status_code == 200
    return dict(r.json())


async def test_whatsapp_failure_releases_dedupe_so_retry_is_processed(client, wa_channel, fakes):
    """Review L-7: the dedupe key used to be set before processing, so Meta's retry of a
    failed message was dropped as a duplicate."""
    body = _wa_body(wa_channel, "wamid.FAIL")
    fakes.chat.error = RuntimeError("model timeout")
    assert (await _post_wa(client, body))["processed"] == 0
    assert not any(v for v in fakes.redis.data.values())  # processing key deleted
    fakes.chat.error = None
    assert (await _post_wa(client, body))["processed"] == 1  # Meta retry handled
    assert list(fakes.redis.data.values()) == ["done"]
    assert (await _post_wa(client, body))["processed"] == 0  # now a real duplicate
    assert len(fakes.chat.calls) == 2


async def test_whatsapp_in_flight_duplicate_is_ignored(client, wa_channel, fakes, two_tenants):
    message_id = "wamid.INFLIGHT"
    digest = hashlib.sha256(message_id.encode()).hexdigest()[:32]
    fakes.redis.data[f"wa:dedupe:{two_tenants[0]}:{digest}"] = "processing"
    assert (await _post_wa(client, _wa_body(wa_channel, message_id)))["processed"] == 0
    assert fakes.chat.calls == []


async def test_whatsapp_rate_limited_per_user(client, wa_channel, fakes, api_settings):
    api_settings.chat_rate_limit_per_minute = 1
    assert (await _post_wa(client, _wa_body(wa_channel, "wamid.1")))["processed"] == 1
    assert (await _post_wa(client, _wa_body(wa_channel, "wamid.2")))["processed"] == 0
    other = _wa_body(wa_channel, "wamid.3", wa_id="5511988887777")
    assert (await _post_wa(client, other))["processed"] == 1
    assert len(fakes.chat.calls) == 2
    # The raw wa_id never appears in limiter keys.
    assert all("5567999990000" not in k for k in fakes.limiter._counts)


# --- L-9: X-Request-ID ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        (str(uuid.uuid4()), True),
        ("0123456789abcdef", True),  # 16 hex
        ("a" * 64, True),
        ("0123456789abcde", False),  # 15 hex
        ("a" * 65, False),
        ("67998123456", False),  # phone number
        ("ana.ferreira", False),
        ("ana@example.com", False),
        ("req-123", False),
    ],
)
async def test_request_id_only_uuid_or_hex(client, value, accepted):
    r = await client.get("/health", headers={"X-Request-ID": value})
    assert (r.headers["X-Request-ID"] == value) is accepted
    if not accepted:
        assert len(r.headers["X-Request-ID"]) == 32


# --- L-11: docs routes ------------------------------------------------------------------


@pytest.mark.parametrize("expose", [True, False])
async def test_docs_routes_follow_setting(api_settings, expose):
    app = create_app(api_settings.model_copy(update={"expose_api_docs": expose}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert ((await c.get(path)).status_code == 200) is expose, path


def test_prod_settings_hide_docs():
    settings = Settings(
        app_env="prod",
        llm_provider="openai",
        embedding_provider="openai",
        api_key_pepper=SecretStr("prod-pepper-xyz"),
        user_ref_key=SecretStr("prod-user-ref-key"),
        expose_api_docs=True,
    )
    assert settings.expose_api_docs is False


# --- L-12a: reprocess a version stuck in processing --------------------------------------


@pytest.mark.parametrize(("age_minutes", "expected"), [(5, 409), (20, 202)])
async def test_reprocess_stuck_processing_version(
    client, session_factory, two_tenants, make_key, fakes, age_minutes, expected
):
    tenant = two_tenants[0]
    async with session_factory() as s:
        doc = Document(tenant_id=tenant, title="Política", source_type="txt")
        s.add(doc)
        await s.flush()
        version = DocumentVersion(
            tenant_id=tenant,
            document_id=doc.id,
            version=1,
            content_hash=uuid.uuid4().hex,
            mime_type="text/plain",
            byte_size=3,
            raw_content=b"abc",
            ingestion_status="processing",
        )
        s.add(version)
        await s.flush()
        await s.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == version.id)
            .values(processed_at=utcnow() - timedelta(minutes=age_minutes))
        )
        await s.commit()
    key = await make_key(tenant)
    r = await client.post(
        f"/v1/knowledge/documents/{doc.id}/versions/{version.id}/reprocess",
        headers={"X-API-Key": key},
    )
    assert r.status_code == expected
    assert (version.id in [v for _, v in fakes.enqueuer.calls]) is (expected == 202)


# --- H-1 wiring: ChatService receives the shared limiter ---------------------------------


async def test_chat_service_gets_shared_limiter(api_settings):
    limiter = InMemoryAttemptLimiter()
    deps._shared.clear()
    try:
        service = deps.get_chat_service(api_settings, limiter)
        assert getattr(service, "limiter", None) is limiter
        assert deps.get_chat_service(api_settings, limiter) is service
    finally:
        await deps.close_shared_clients()
