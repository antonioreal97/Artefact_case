"""Offline regressions for English-customer guardrail and policy fixes (v9)."""

from decimal import Decimal

import pytest

from app.agents.guardrails import (
    OUT_OF_SCOPE,
    OUT_OF_SCOPE_EN,
    admits_no_information,
    is_out_of_scope_refusal,
    money_amounts,
    output_violation,
)
from app.agents.instructions import POLICY_TEXT, PROMPT_VERSION
from app.tools.context import ToolCallRecord


def _catalog(*prices: tuple[str, str]) -> list[ToolCallRecord]:
    items = [{"name": name, "price": price, "active_promotion": None} for name, price in prices]
    return [
        ToolCallRecord(
            tool_name="search_products",
            arguments_redacted={},
            status="success",
            error_code=None,
            result_summary={"ok": True, "data": {"items": items, "total": len(items)}},
            latency_ms=1,
        )
    ]


def _violation(
    answer: str,
    *,
    records: list[ToolCallRecord] | None = None,
    current_message: str = "",
) -> str | None:
    return output_violation(
        answer,
        records=records or [],
        labels=set(),
        instructions="",
        policy_text="",
        current_message=current_message,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("R$599.90", [Decimal("599.90")]),
        ("R$ 599,90", [Decimal("599.90")]),
        ("R$1,000", [Decimal("1000")]),
        ("R$1,000.00", [Decimal("1000")]),
        ("R$ 1,000", [Decimal("1000")]),
        ("up to R$1,000", [Decimal("1000")]),
        ("R$ 1.000,00", [Decimal("1000")]),
        ("até R$1.000", [Decimal("1000")]),
    ],
)
def test_money_amounts_accept_en_us_and_pt_br(text, expected):
    assert money_amounts(text) == expected


@pytest.mark.parametrize(
    "budget",
    ["R$1,000", "R$1,000.00", "R$ 1,000", "R$1000", "R$ 1.000", "R$ 1.000,00"],
)
def test_english_budget_echo_with_grounded_prices(budget):
    records = _catalog(("Yamaha C40 Nylon Natural", "599.90"))
    answer = f"Acoustic guitars for up to {budget}: Yamaha C40 Nylon Natural at R$599.90."
    assert (
        _violation(
            answer,
            records=records,
            current_message=f"Which acoustic guitars do you have for up to {budget}?",
        )
        is None
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Yamaha C40 Nylon Natural costs R$1,000.00.",
        "Yamaha C40 Nylon Natural: R$699.90.",
        "The Yamaha C40 Nylon Natural is R$1,000.",
        "Yamaha C40 Nylon Natural custa R$ 1.000,00.",
    ],
)
def test_english_format_wrong_price_still_fails(answer):
    records = _catalog(("Yamaha C40 Nylon Natural", "599.90"))
    assert (
        _violation(
            answer,
            records=records,
            current_message="Which guitars for up to R$1,000?",
        )
        == "price"
    )


def test_portuguese_budget_path_unchanged():
    records = _catalog(("Yamaha C40", "800.00"))
    assert (
        _violation(
            "Violões disponíveis (até R$1.000): Yamaha C40 por R$ 800,00.",
            records=records,
            current_message="Quais violões custam até R$ 1.000?",
        )
        is None
    )
    assert (
        _violation(
            "Yamaha C40 custa R$ 1.000.",
            records=records,
            current_message="Tenho R$1000",
        )
        == "price"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Sorry — I couldn't find the store's documented return policy.",
        "I couldn't find any information about returns.",
        "I can't find the exact terms in our manuals.",
        "I don't have that information.",
        "I won't guess those details.",
        "Não encontrei essa informação.",
        "Não tenho essa informação no momento.",
    ],
)
def test_admits_no_information_english_and_portuguese(text):
    assert admits_no_information(text)


def test_admits_no_information_negatives():
    assert not admits_no_information("Não foi possível confirmar os dados do pedido.")
    assert not admits_no_information("The Yamaha C40 costs R$599.90.")
    assert not admits_no_information("I can help with products and prices.")


@pytest.mark.parametrize("reply", [OUT_OF_SCOPE, OUT_OF_SCOPE_EN])
def test_bilingual_scope_refusal_is_recognized(reply):
    assert is_out_of_scope_refusal(reply)
    assert (
        output_violation(
            reply,
            records=[],
            labels=set(),
            instructions=POLICY_TEXT,
            policy_text=POLICY_TEXT,
        )
        is None
    )


@pytest.mark.parametrize(
    "reply",
    [
        "Sorry, I can only help with store-related topics.",
        "I can only help with store-related topics. "
        "I can help with products, prices, stock, orders, and store policies.",
        "Desculpe, posso ajudar apenas com assuntos da loja.",
    ],
)
def test_bounded_scope_variants(reply):
    assert is_out_of_scope_refusal(reply)


@pytest.mark.parametrize(
    "extra",
    [" The price is R$100.", " We have 3 units in stock.", " [S1]", " Delivery in 7 days."],
)
def test_english_scope_prefix_does_not_exempt_claims(extra):
    answer = OUT_OF_SCOPE_EN + extra
    assert not is_out_of_scope_refusal(answer)


def test_prompt_version_and_policy_cover_english_rules():
    assert PROMPT_VERSION == "commerce-privacy-v10"
    assert OUT_OF_SCOPE_EN in POLICY_TEXT
    assert "traduza a consulta" in POLICY_TEXT.casefold()
    assert "Nunca ofereça ações sem ferramenta" in POLICY_TEXT
    assert "INTEIRAMENTE no idioma" in POLICY_TEXT
