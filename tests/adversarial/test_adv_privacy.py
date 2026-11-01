"""Attack 5: no raw PII reaches the model, logs, or the redacted DB columns – across
multi-turn conversations, tool outputs and history."""

from __future__ import annotations

import json
import uuid

import pytest
from adv_support import CPF, World, chat, history_lookup_model, leaked, restricted_values
from sqlalchemy import select

from app.models import AuditEvent, HandoffRequest, Message, ToolExecution
from app.services.fake_model import FakeCommerceModel

pytestmark = pytest.mark.integration


async def _db_dump(session_factory, tenant_id: uuid.UUID) -> str:
    """Every column that is documented as redacted-only (plus pre-auth audit events)."""
    async with session_factory() as s:
        tools = await s.scalars(select(ToolExecution).where(ToolExecution.tenant_id == tenant_id))
        audits = await s.scalars(
            select(AuditEvent).where(
                (AuditEvent.tenant_id == tenant_id) | (AuditEvent.tenant_id.is_(None))
            )
        )
        handoffs = await s.scalars(
            select(HandoffRequest).where(HandoffRequest.tenant_id == tenant_id)
        )
        messages = await s.scalars(select(Message).where(Message.tenant_id == tenant_id))
        blob = {
            "tool_executions": [
                [t.arguments_redacted, t.result_summary, t.error_code] for t in tools
            ],
            "audit_events": [[a.event_type, a.data] for a in audits],
            "handoffs": [h.summary for h in handoffs],
            "content_redacted": [m.content_redacted for m in messages],
        }
    return json.dumps(blob, ensure_ascii=False, default=str)


async def test_multi_turn_order_flow_never_egresses_raw_pii(
    client, world: World, model_control, session_factory, logs
) -> None:
    d = world.a.data
    t1 = await chat(client, world.a, f"Oi, meu pedido {d.order_id} ainda não chegou")
    conv = t1["conversation_id"]
    await chat(
        client,
        world.a,
        f"Meu e-mail é {d.email} e meu telefone {d.phone}",
        conv,
    )
    await chat(client, world.a, f"Se precisar, meu CPF é {CPF}", conv)
    # Turn 3: a model that combines tokens from turns 1 and 2 to look the order up.
    model_control.factory = history_lookup_model
    t3 = await chat(client, world.a, "Pode verificar agora?", conv)
    assert d.tracking in t3["reply"], t3  # control: tokens resolved locally and revealed
    model_control.factory = FakeCommerceModel
    await chat(client, world.a, f"Aqui é {d.customer_name}. Qual o horário de funcionamento?", conv)
    await chat(client, world.a, f"Quanto custa a {d.product_name}?", conv)

    raw = [*restricted_values(d), CPF]
    payloads = model_control.payloads()
    assert "[[ORDER_1]]" in payloads and "[[EMAIL_1]]" in payloads  # tokens did flow
    assert not leaked(payloads, raw), leaked(payloads, raw)
    assert not leaked(await _db_dump(session_factory, world.a.id), raw)
    assert not leaked(logs.text(), raw), leaked(logs.text(), raw)


async def test_failed_lookup_and_handoff_paths_store_no_pii(
    client, world: World, model_control, session_factory, logs
) -> None:
    d = world.a.data
    await chat(client, world.a, f"pedido {d.order_id} email errado.{d.email}")
    await chat(client, world.a, f"Quero falar com um atendente, sou {d.customer_name}, {d.phone}")
    await chat(client, world.a, f"Tenho uma reclamação do pedido {d.order_id}, {d.email}")
    raw = restricted_values(d)
    assert not leaked(model_control.payloads(), raw)
    assert not leaked(await _db_dump(session_factory, world.a.id), raw)
    assert not leaked(logs.text(), raw)


# Each value is restricted personal data written in an ordinary way by a customer.
UNDETECTED_FORMATS = [
    pytest.param(
        "Qual o status? pedido: 481516",
        "481516",
        id="order-id-with-colon",
    ),
    pytest.param(
        "O número do meu pedido é 481516, pode ver?",
        "481516",
        id="order-id-in-prose",
    ),
    pytest.param(
        "Meu telefone é (11) 99999.8888",
        "99999.8888",
        id="phone-with-dots",
    ),
    pytest.param(
        "Meu documento é 12345678909, preciso de ajuda com a garantia",
        "12345678909",
        id="bare-cpf",
    ),
    pytest.param(
        "Meu código de rastreio LX123456789BR não atualiza",
        "LX123456789BR",
        id="correios-tracking",
    ),
    pytest.param(
        "Meu nome é Joana Prado Lima e quero saber da garantia",
        "Joana Prado Lima",
        id="name-not-in-crm",
    ),
    pytest.param("Meu e-mail é Fulano.Tal+loja@Example.COM.br", "Fulano.Tal", id="email"),
    pytest.param("Me liga no +55 (11) 98765-4321", "98765-4321", id="phone-intl"),
    pytest.param("Meu CPF é 987.654.321-00", "987.654.321-00", id="cpf-formatted"),
    pytest.param("Rastreio BRXY1234567BR", "BRXY1234567BR", id="br-tracking"),
    pytest.param("CEP 79002-970 é o meu", "79002-970", id="cep"),
]


@pytest.mark.parametrize(("text", "raw"), UNDETECTED_FORMATS)
async def test_common_pii_formats_do_not_reach_model(
    client, world: World, model_control, text: str, raw: str
) -> None:
    await chat(client, world.a, text)
    assert raw.casefold() not in model_control.payloads().casefold()


async def test_api_handoff_summary_redacts_pattern_pii(client, world: World) -> None:
    d = world.a.data
    conv = (await chat(client, world.a, "Olá"))["conversation_id"]
    created = await client.post(
        "/v1/handoffs",
        headers=world.a.headers,
        json={
            "conversation_id": conv,
            "reason": "customer_request",
            "summary": f"Cliente com e-mail {d.email}, telefone {d.phone}, rastreio {d.tracking}",
        },
    )
    assert created.status_code == 201, created.text
    assert not leaked(created.json()["summary"], [d.email, d.phone, d.tracking])


async def test_api_handoff_summary_redacts_known_customer_name_and_order(client, world: World):
    d = world.a.data
    conv = (await chat(client, world.a, "Olá"))["conversation_id"]
    created = await client.post(
        "/v1/handoffs",
        headers=world.a.headers,
        json={
            "conversation_id": conv,
            "reason": "customer_request",
            "summary": f"{d.customer_name} reclamou do pedido: {d.order_id}",
        },
    )
    assert created.status_code == 201, created.text
    assert not leaked(created.json()["summary"], [d.customer_name, str(d.order_id)])
