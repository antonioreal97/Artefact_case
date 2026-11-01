"""Independent stock contradictions and Portuguese catalog-routing regressions."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.guardrails import SAFE_FALLBACK, answer_uses_tool_facts, output_violation
from app.core.config import Settings
from app.models import CommerceProduct
from app.services.attempt_limiter import InMemoryAttemptLimiter
from app.services.chat import ChatService
from app.services.fake_model import FakeCommerceModel
from app.services.model_gateway import GuardedModelProvider
from app.services.vault_store import InMemoryVaultStore
from app.tools.context import ToolCallRecord


def stock_record(quantity: int, available: bool, *, tool: str = "check_stock") -> ToolCallRecord:
    data: dict[str, object] = {
        "name": "Yamaha C40",
        "stock_quantity": quantity,
        "available": available,
    }
    return ToolCallRecord(
        tool_name=tool,
        arguments_redacted={},
        status="success",
        error_code=None,
        result_summary={"data": {"items": [data]} if tool == "search_products" else data},
        latency_ms=0,
    )


@pytest.mark.parametrize("tool", ["check_stock", "search_products"])
@pytest.mark.parametrize(
    ("quantity", "available", "answer"),
    [
        (0, False, "Yamaha C40: disponível em estoque, com 99 unidades."),
        (0, False, "Yamaha C40: 99 unidades."),
        (0, False, "Yamaha C40: disponível."),
        (4, True, "Yamaha C40: indisponível."),
        (4, True, "Yamaha C40: não está em estoque."),
        (4, True, "Yamaha C40: esgotado."),
        (4, True, "Yamaha C40: estoque: 99."),
    ],
)
def test_stock_contradictions(quantity: int, available: bool, answer: str, tool: str) -> None:
    records = [stock_record(quantity, available, tool=tool)]
    assert (
        output_violation(
            answer,
            records=records,
            labels=set(),
            instructions="",
            policy_text="",
        )
        == "stock"
    )
    assert not answer_uses_tool_facts(answer, records)


@pytest.mark.parametrize(
    ("quantity", "available", "answer"),
    [
        (0, False, "Yamaha C40: indisponível, com 0 unidades."),
        (4, True, "Yamaha C40: disponível em estoque, com 4 unidades."),
        (0, False, "Yamaha C40: não está disponível."),
        (0, False, "Yamaha C40: não está disponível em estoque."),
    ],
)
def test_correct_stock_passes(quantity: int, available: bool, answer: str) -> None:
    records = [stock_record(quantity, available)]
    assert (
        output_violation(
            answer,
            records=records,
            labels=set(),
            instructions="",
            policy_text="",
        )
        is None
    )
    assert answer_uses_tool_facts(answer, records)


def test_search_without_quantity_cannot_back_quantity() -> None:
    record = stock_record(4, True, tool="search_products")
    record.result_summary = {"data": {"items": [{"name": "Yamaha C40", "available": True}]}}
    assert (
        output_violation(
            "Yamaha C40: 4 unidades.",
            records=[record],
            labels=set(),
            instructions="",
            policy_text="",
        )
        == "stock"
    )


def test_quantities_are_bound_to_named_product() -> None:
    a, b = stock_record(0, False), stock_record(99, True)
    b.result_summary = {"data": {"name": "Takamine GD20", "available": True, "stock_quantity": 99}}
    assert (
        output_violation(
            "Yamaha C40: 99 unidades; Takamine GD20: 0 unidades.",
            records=[a, b],
            labels=set(),
            instructions="",
            policy_text="",
        )
        == "stock"
    )


@pytest.mark.parametrize(
    ("price_phrase", "expected"),
    [
        ("até R$1000", 1000),
        ("abaixo de R$ 1.000,50", 1000.5),
        ("no máximo R$ 1.000", 1000),
        ("até 999,90 reais", 999.9),
    ],
)
def test_catalog_filters(price_phrase: str, expected: float) -> None:
    output = FakeCommerceModel()._step(
        f"Quais opções de violões disponíveis custando {price_phrase}?"
    )[0]
    assert output.type == "function_call"
    assert output.name == "search_products"
    assert json.loads(output.arguments) == {
        "query": "violão",
        "max_price": expected,
        "in_stock_only": True,
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    "answer",
    [
        "Yamaha C40: disponível em estoque, com 99 unidades.",
        "Yamaha C40: 99 unidades.",
    ],
)
async def test_stock_mismatch_falls_back_and_hands_off(
    session_factory: async_sessionmaker[AsyncSession],
    two_tenants: tuple[uuid.UUID, uuid.UUID],
    answer: str,
) -> None:
    tenant, _ = two_tenants
    async with session_factory() as session:
        session.add(
            CommerceProduct(
                tenant_id=tenant,
                external_id=1,
                category_external_id=1,
                name="Yamaha C40",
                price=Decimal("800"),
                stock_quantity=0,
                status="active",
            )
        )
        await session.commit()
    model = ScriptedModel(
        [
            [function_call("check_stock", {"product_id": 1}, call_id="stock")],
            [assistant_message(answer)],
        ]
    )
    service = ChatService(
        session_factory,
        Settings(app_env="test", llm_provider="fake", embedding_provider="hash"),
        InMemoryVaultStore(),
        lambda settings, allowlist: GuardedModelProvider(model, allowlist),
        limiter=InMemoryAttemptLimiter(),
    )
    async with session_factory() as session:
        reply = await service.handle_message(
            session,
            tenant_id=tenant,
            conversation_id=None,
            text="Yamaha C40 em estoque?",
            correlation_id="stock-regression",
        )
    assert reply.reply.startswith(SAFE_FALLBACK)
    assert not reply.grounded
    assert reply.handoff and reply.handoff.reason == "low_confidence"
    assert "99" not in reply.reply


def test_documents_cannot_override_product_stock() -> None:
    answer = "Yamaha C40: disponível em estoque durante a promoção."
    assert (
        output_violation(
            answer,
            records=[stock_record(0, False)],
            labels=set(),
            instructions="",
            policy_text="",
            documents=[answer],
        )
        == "stock"
    )


def test_generic_cited_stock_condition_is_not_a_current_stock_claim() -> None:
    policy = "Produtos esgotados durante a promoção não geram direito a reserva de preço."
    assert (
        output_violation(
            policy + " [S1]",
            records=[],
            labels={"S1"},
            instructions="",
            policy_text="",
            documents=[policy],
        )
        is None
    )


def test_no_current_turn_stock_evidence_fails_closed() -> None:
    assert (
        output_violation(
            "Yamaha C40: disponível em estoque.",
            records=[],
            labels=set(),
            instructions="",
            policy_text="",
        )
        == "stock"
    )
