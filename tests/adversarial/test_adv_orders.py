"""Attacks 4 and 7: catalog/order leakage across tenants, order enumeration, brute force."""

from __future__ import annotations

import uuid

import pytest
from adv_support import World, chat, leaked, scripted
from agents.testing import assistant_message, function_call
from sqlalchemy import select

from app.models import ToolExecution

pytestmark = pytest.mark.integration


def _order_msg(order_id: int, contact: str) -> str:
    return f"Quero saber do meu pedido {order_id}, meu contato é {contact}"


def _secrets(world: World) -> list[str]:
    d = world.a.data
    return [d.tracking, d.delivery.isoformat(), d.delivery.strftime("%d/%m/%Y")]


async def test_control_owner_can_look_up_own_order(client, world: World) -> None:
    """Sanity check so the negative tests below are not vacuous."""
    reply = await chat(client, world.a, _order_msg(world.a.data.order_id, world.a.data.email))
    assert world.a.data.tracking in reply["reply"], reply


async def test_other_tenant_cannot_read_products_by_name(client, world: World) -> None:
    question = f"Qual o preço da {world.a.data.product_name}?"
    own = await chat(client, world.a, question)
    assert world.a.data.product_price_text in own["reply"], own  # control
    reply = await chat(client, world.b, question)
    assert not leaked(
        reply["reply"],
        [world.a.data.product_name, world.a.data.product_price_text, world.a.data.marker],
    )
    stock = await chat(client, world.b, f"{world.a.data.product_name} disponível em estoque?")
    assert world.a.data.product_name not in stock["reply"]


async def test_colliding_product_ids_resolve_to_own_tenant(client, world: World, model_control):
    """Both tenants have external product id 1; a model asking for id 1 in tenant B must get
    B's product only."""
    model_control.factory = scripted(
        [function_call("get_product_details", {"product_id": 1}, call_id="d1")],
        [function_call("check_stock", {"product_id": 1}, call_id="s1")],
        [function_call("get_price", {"product_id": 1}, call_id="p1")],
        [assistant_message("Consultei o produto 1.")],
    )
    before = len(model_control.calls)
    await chat(client, world.b, "Detalhes do produto 1")
    payloads = model_control.payloads_since(before)
    assert world.b.data.product_name in payloads  # control: B's own product was read
    assert not leaked(
        payloads, [world.a.data.product_name, world.a.data.marker, world.a.data.product_price_text]
    )


async def test_other_tenant_cannot_look_up_order_with_correct_contact(client, world: World):
    reply = await chat(client, world.b, _order_msg(world.a.data.order_id, world.a.data.email))
    assert not leaked(reply["reply"], _secrets(world))
    assert reply["grounded"] is False


async def test_nonexistent_order_and_wrong_contact_are_indistinguishable(
    client, world: World, session_factory
) -> None:
    d = world.a.data
    missing = await chat(client, world.a, _order_msg(999_999, d.email))
    wrong = await chat(client, world.a, _order_msg(d.order_id, "outra.pessoa@example.test"))
    foreign = await chat(client, world.b, _order_msg(d.order_id, d.email))
    shapes = [
        (
            r["reply"],
            r["grounded"],
            r["tools_used"],
            r["tool_errors"],
            r["handoff"]["reason"] if r["handoff"] else None,
        )
        for r in (missing, wrong, foreign)
    ]
    assert shapes[0] == shapes[1] == shapes[2], shapes
    async with session_factory() as s:
        codes = {
            row.message_id: row.error_code
            for row in await s.scalars(
                select(ToolExecution).where(ToolExecution.tool_name == "lookup_order")
            )
        }
    assert (
        codes[uuid.UUID(missing["message_id"])]
        == codes[uuid.UUID(wrong["message_id"])]
        == codes[uuid.UUID(foreign["message_id"])]
    )


async def test_lockout_after_three_failures_in_same_conversation(client, world: World) -> None:
    d = world.a.data
    conv = None
    for attempt in range(3):
        r = await chat(
            client, world.a, _order_msg(d.order_id, f"chute{attempt}@example.test"), conv
        )
        conv = r["conversation_id"]
    final = await chat(client, world.a, _order_msg(d.order_id, d.email), conv)
    assert not leaked(final["reply"], _secrets(world)), final


async def test_lockout_survives_new_conversations(client, world: World) -> None:
    """Brute force: an attacker simply omits conversation_id to get a fresh conversation for
    every guess. Failures must still count per (tenant, order)."""
    d = world.a.data
    for attempt in range(3):
        await chat(client, world.a, _order_msg(d.order_id, f"chute{attempt}@example.test"))
    final = await chat(client, world.a, _order_msg(d.order_id, d.email))
    assert not leaked(final["reply"], _secrets(world)), final


async def test_lockout_is_per_tenant(client, world: World) -> None:
    """Failures against tenant B's order space must not lock tenant A's customer out (and
    the counters must not be shared across tenants with colliding ids)."""
    for attempt in range(3):
        await chat(client, world.b, _order_msg(world.a.data.order_id, f"x{attempt}@example.test"))
    reply = await chat(client, world.a, _order_msg(world.a.data.order_id, world.a.data.email))
    assert world.a.data.tracking in reply["reply"], reply


async def test_wrong_guesses_on_other_orders_do_not_lock_out_other_customers(
    client, world: World
) -> None:
    """Availability: an attacker who guesses three OTHER order numbers (each in a fresh API
    conversation) must not lock every other customer of the tenant out of order lookups."""
    for guess in (111_111, 222_222, 333_333):
        await chat(client, world.a, _order_msg(guess, "atacante@example.test"))
    victim = await chat(client, world.a, _order_msg(world.a.data.order_id, world.a.data.email))
    assert world.a.data.tracking in victim["reply"], victim
