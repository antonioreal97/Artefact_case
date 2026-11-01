"""WhatsApp webhook adapter (validated only with fakes, not against the live Meta API)."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.integrations.meta_whatsapp import MetaWhatsAppClient
from app.models import Conversation, TenantChannel

pytestmark = pytest.mark.integration

URL = "/v1/webhooks/whatsapp"
WA_ID = "5511987654321"


def _payload(phone_number_id: str, msg_id: str, text: str = "Vocês abrem domingo?") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [{"wa_id": WA_ID, "profile": {"name": "Maria"}}],
                            "messages": [
                                {
                                    "id": msg_id,
                                    "from": WA_ID,
                                    "type": "text",
                                    "text": {"body": text},
                                },
                                {"id": msg_id + "-img", "from": WA_ID, "type": "image"},
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _signed(body: bytes, secret: str = "app-secret") -> dict[str, str]:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"}


@pytest.fixture
async def channel(two_tenants, session_factory):
    phone_number_id = f"pn-{two_tenants[0].hex[:10]}"
    async with session_factory() as s:
        s.add(
            TenantChannel(tenant_id=two_tenants[0], channel="whatsapp", external_id=phone_number_id)
        )
        await s.commit()
    return phone_number_id


async def test_verify_handshake(client):
    ok = await client.get(
        URL,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "1158201444",
        },
    )
    assert ok.status_code == 200 and ok.text == "1158201444"
    assert ok.headers["content-type"].startswith("text/plain")
    bad = await client.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}
    )
    assert bad.status_code == 403
    wrong_mode = await client.get(
        URL, params={"hub.mode": "x", "hub.verify_token": "verify-me", "hub.challenge": "1"}
    )
    assert wrong_mode.status_code == 403


async def test_not_configured_503(app, client, api_settings):
    unconfigured = api_settings.model_copy(
        update={"whatsapp_verify_token": None, "whatsapp_app_secret": None}
    )
    app.dependency_overrides[get_settings] = lambda: unconfigured
    r = await client.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"}
    )
    assert r.status_code == 503
    body = b"{}"
    assert (await client.post(URL, content=body, headers=_signed(body))).status_code == 503


async def test_bad_signature_401(client, channel, fakes):
    body = json.dumps(_payload(channel, "wamid.1")).encode()
    r = await client.post(URL, content=body, headers=_signed(body, secret="wrong"))
    assert r.status_code == 401
    r = await client.post(URL, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 401
    tampered = body.replace(b"domingo", b"segunda")
    r = await client.post(URL, content=tampered, headers=_signed(body))
    assert r.status_code == 401
    assert fakes.chat.calls == [] and fakes.whatsapp.sent == []


async def test_good_signature_routes_to_chat_and_replies(
    client, channel, fakes, two_tenants, session_factory, api_settings
):
    body = json.dumps(_payload(channel, "wamid.A")).encode()
    r = await client.post(URL, content=body, headers=_signed(body))
    assert r.status_code == 200 and r.json() == {"status": "ok", "processed": 1}

    call = fakes.chat.calls[-1]
    assert call["tenant_id"] == two_tenants[0]
    assert call["channel"] == "whatsapp"
    assert call["conversation_id"] is None
    assert call["text"] == "Vocês abrem domingo?"
    expected_ref = hashlib.sha256(
        (api_settings.api_key_pepper.get_secret_value() + WA_ID).encode()
    ).hexdigest()
    assert call["external_user_ref"] == expected_ref and WA_ID not in expected_ref
    assert fakes.whatsapp.sent == [
        {"phone_number_id": channel, "to": WA_ID, "text": "Trocas em até 7 dias. [S1]"}
    ]

    # Meta retry of the same message id is deduplicated
    again = await client.post(URL, content=body, headers=_signed(body))
    assert again.json() == {"status": "ok", "processed": 0}
    assert len(fakes.chat.calls) == 1

    # a new message from the same user continues the same open conversation
    body2 = json.dumps(_payload(channel, "wamid.B", "E sábado?")).encode()
    await client.post(URL, content=body2, headers=_signed(body2))
    assert len(fakes.chat.calls) == 2
    async with session_factory() as s:
        convs = (
            await s.scalars(
                select(Conversation).where(
                    Conversation.tenant_id == two_tenants[0], Conversation.channel == "whatsapp"
                )
            )
        ).all()
    assert len(convs) == 1
    assert fakes.chat.calls[1]["conversation_id"] == convs[0].id
    assert all(WA_ID not in (c.external_user_ref or "") for c in convs)


async def test_unknown_channel_and_malformed_payloads_still_200(client, fakes):
    body = json.dumps(_payload("unregistered-number", "wamid.Z")).encode()
    r = await client.post(URL, content=body, headers=_signed(body))
    assert r.status_code == 200 and r.json()["processed"] == 0
    for raw in (b"not json", b"[]", b'{"entry": "x"}', b'{"entry": [{"changes": [1]}]}'):
        r = await client.post(URL, content=raw, headers=_signed(raw))
        assert r.status_code == 200, raw
    assert fakes.chat.calls == []


async def test_chat_failure_still_200_and_redis_down_degrades(client, channel, fakes):
    fakes.redis.fail = True
    fakes.chat.error = RuntimeError("model exploded")
    body = json.dumps(_payload(channel, "wamid.F")).encode()
    r = await client.post(URL, content=body, headers=_signed(body))
    assert r.status_code == 200 and r.json()["processed"] == 0
    assert len(fakes.chat.calls) == 1  # processed despite Redis being down
    assert fakes.whatsapp.sent == []


async def test_meta_client_payload_and_unconfigured():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    settings = Settings(app_env="test", whatsapp_access_token=SecretStr("tok"))
    client = MetaWhatsAppClient(
        settings, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert await client.send_text(phone_number_id="123", to=WA_ID, text="oi") is True
    request = seen[0]
    assert str(request.url) == "https://graph.facebook.com/v21.0/123/messages"
    assert request.headers["Authorization"] == "Bearer tok"
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": WA_ID,
        "type": "text",
        "text": {"preview_url": False, "body": "oi"},
    }
    await client.aclose()

    failing = MetaWhatsAppClient(
        settings,
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    assert await failing.send_text(phone_number_id="123", to=WA_ID, text="oi") is False
    await failing.aclose()

    unconfigured = MetaWhatsAppClient(Settings(app_env="test", whatsapp_access_token=None))
    assert unconfigured.configured is False
    assert await unconfigured.send_text(phone_number_id="1", to=WA_ID, text="x") is False
    await unconfigured.aclose()
