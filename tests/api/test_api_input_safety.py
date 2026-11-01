"""Adversarial FINDING-3 (handoff summaries keep names/order ids) and FINDING-6 (NUL bytes
in text fields cause HTTP 500) regressions for the HTTP layer."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models import CommerceCustomer, CommerceOrder, Conversation, HandoffRequest, TenantChannel

pytestmark = pytest.mark.integration

NAME = "Ana Pessoa Teste"
EMAIL = "ana.teste@example.test"
PHONE = "(67) 98888-1234"
ORDER_ID = 481516
OTHER_TENANT_ORDER = 777001


@pytest.fixture
async def world(session_factory, two_tenants, make_key):
    a, b = two_tenants
    suffix = uuid.uuid4().int % 10_000
    async with session_factory() as s:
        conv = Conversation(tenant_id=a, channel="api")
        s.add_all(
            [
                conv,
                CommerceCustomer(
                    tenant_id=a, external_id=suffix, name=NAME, phone=PHONE, email=EMAIL
                ),
                CommerceOrder(
                    tenant_id=a,
                    external_id=ORDER_ID + suffix,
                    customer_external_id=suffix,
                    order_date=date(2026, 10, 1),
                    status="shipped",
                    total=Decimal("10.00"),
                    payment_method="pix",
                ),
                CommerceCustomer(
                    tenant_id=b,
                    external_id=suffix,
                    name="Bruno Outro Tenant",
                    phone="(11) 97777-0000",
                    email="bruno@other.test",
                ),
                CommerceOrder(
                    tenant_id=b,
                    external_id=OTHER_TENANT_ORDER + suffix,
                    customer_external_id=suffix,
                    order_date=date(2026, 10, 1),
                    status="shipped",
                    total=Decimal("10.00"),
                    payment_method="pix",
                ),
            ]
        )
        await s.commit()
    headers = {"X-API-Key": await make_key(a)}
    return {
        "headers": headers,
        "conversation_id": str(conv.id),
        "order": ORDER_ID + suffix,
        "other_order": OTHER_TENANT_ORDER + suffix,
    }


async def _handoff(client, world, summary: str):
    return await client.post(
        "/v1/handoffs",
        headers=world["headers"],
        json={
            "conversation_id": world["conversation_id"],
            "reason": "customer_request",
            "summary": summary,
        },
    )


# --- FINDING-3 ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "{name} reclamou do pedido: {order}",  # adversarial repro
        "O número do meu pedido é {order}, sou {name}",
        "Cliente Ana Teste citou {order} sem dizer a palavra",  # bare known order id
        "Contato {email} / {phone}; cliente {name}",
    ],
)
async def test_handoff_summary_has_no_name_or_order_id(
    client, world, session_factory, template: str
):
    summary = template.format(name=NAME, order=world["order"], email=EMAIL, phone=PHONE)
    created = await _handoff(client, world, summary)
    assert created.status_code == 201, created.text
    returned = created.json()["summary"]
    async with session_factory() as s:
        stored = (await s.get(HandoffRequest, uuid.UUID(created.json()["id"]))).summary
    assert stored == returned
    for raw in (NAME, "Ana Teste", str(world["order"]), EMAIL, "98888", "1234"):
        assert raw.casefold() not in returned.casefold(), raw
    assert "[REDACTED_" in returned


async def test_handoff_summary_keeps_harmless_text_and_is_tenant_scoped(client, world):
    # Another tenant's order id is not a restricted value of THIS tenant (tenant filter),
    # and ordinary text survives.
    summary = f"Dúvida sobre garantia de 12 meses; referência {world['other_order']}"
    created = await _handoff(client, world, summary)
    assert created.status_code == 201
    assert created.json()["summary"] == summary


# --- FINDING-6 ----------------------------------------------------------------------------


def _assert_nul_422(response) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "validation_error"
    assert "\x00" not in response.text and "\\u0000" not in response.text
    assert any("NUL" in err["msg"] for err in body["detail"])


async def test_nul_in_chat_message_is_422(client, world, fakes):
    r = await client.post(CHAT, headers=world["headers"], json={"message": "Olá\x00 tudo bem?"})
    _assert_nul_422(r)
    assert fakes.chat.calls == []


async def test_nul_in_handoff_summary_is_422(client, world):
    _assert_nul_422(await _handoff(client, world, "resumo\x00"))


@pytest.mark.parametrize("field", ["title", "category", "source_url"])
async def test_nul_in_text_document_fields_is_422(client, world, fakes, field: str):
    body = {"title": "Trocas", "content": "Trocas em 7 dias.", field: "x\x00y"}
    r = await client.post("/v1/knowledge/documents/text", headers=world["headers"], json=body)
    _assert_nul_422(r)
    assert fakes.enqueuer.calls == []


@pytest.mark.parametrize("field", ["title", "category", "source_url"])
async def test_nul_in_multipart_fields_is_422(client, world, fakes, field: str):
    form = {"title": "Trocas", field: "x\x00y"}
    r = await client.post(
        "/v1/knowledge/documents",
        headers=world["headers"],
        data=form,
        files={"file": ("ok.txt", b"Politica de trocas.", "text/plain")},
    )
    _assert_nul_422(r)
    assert fakes.enqueuer.calls == []


async def test_nul_in_upload_filename_is_stripped(client, world, fakes):
    r = await client.post(
        "/v1/knowledge/documents",
        headers=world["headers"],
        data={"title": "Trocas"},
        files={"file": ("ok\x00.txt", b"Politica de trocas.", "text/plain")},
    )
    assert r.status_code == 202, r.text


async def test_nul_in_whatsapp_text_is_dropped_and_acked(
    client, session_factory, two_tenants, fakes
):
    phone_number_id = f"pn-{uuid.uuid4().hex[:10]}"
    async with session_factory() as s:
        s.add(
            TenantChannel(tenant_id=two_tenants[0], channel="whatsapp", external_id=phone_number_id)
        )
        await s.commit()

    async def post(message_id: str, text: str) -> dict[str, object]:
        message = {"id": message_id, "from": "5567900001111", "type": "text"}
        message["text"] = {"body": text}  # type: ignore[assignment]
        value = {"metadata": {"phone_number_id": phone_number_id}, "messages": [message]}
        body = json.dumps({"entry": [{"changes": [{"value": value}]}]}).encode()
        sig = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        r = await client.post(
            "/v1/webhooks/whatsapp",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        return dict(r.json())

    assert (await post("wamid.NUL1", "Olá\x00 tudo bem?"))["processed"] == 1
    assert fakes.chat.calls[-1]["text"] == "Olá tudo bem?"
    assert (await post("wamid.NUL2", "\x00"))["processed"] == 0  # nothing left: ignored
    assert len(fakes.chat.calls) == 1


CHAT = "/v1/chat/messages"
