"""Pure-function regressions for the R2 adversarial review (tests/adversarial/FINDINGS.md)."""

from __future__ import annotations

import pytest

from app.agents.guardrails import output_violation
from app.agents.instructions import POLICY_TEXT
from app.services.privacy import PrivacyBoundaryError, redact_user_text, scan_outbound
from app.services.privacy_vault import PrivacyVault
from app.tools.context import ToolCallRecord
from emporio_agent.privacy import (
    find_restricted_data,
    is_valid_cpf,
    redact_for_log,
)


# --- FINDING-1: bare CPF -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("12345678909", True),
        ("123.456.789-09", True),
        ("52998224725", True),
        ("12345678900", False),  # wrong check digits
        ("11111111111", False),  # repeated digits pass the arithmetic but are invalid
        ("1234567890", False),
    ],
)
def test_finding1_cpf_check_digits(value, valid):
    assert is_valid_cpf(value) is valid


def test_finding1_bare_cpf_is_tokenized_and_blocked():
    vault = PrivacyVault()
    text = "Meu documento é 12345678909, preciso de ajuda com a garantia"
    redacted = redact_user_text(text, vault)
    assert "12345678909" not in redacted and "[[CPF_1]]" in redacted
    assert vault.reveal(redacted) == text
    assert {f.kind for f in find_restricted_data(text)} == {"cpf"}
    assert "12345678909" not in redact_for_log(text)
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound({"input": text})


def test_finding1_phone_shapes_stay_phones_and_other_numbers_pass():
    assert "[[PHONE_1]]" in redact_user_text("Me liga no 11987654321", PrivacyVault())
    # 11 digits with invalid check digits, glued to letters, or inside amounts: not a CPF.
    for text in ("Código 12345678900", "ref x12345678909", "R$ 12.345.678,90"):
        assert not [f for f in find_restricted_data(text) if f.kind == "cpf"], text
        assert "CPF" not in redact_user_text(text, PrivacyVault())


# --- FINDING-2: S10 tracking codes ---------------------------------------------------------
@pytest.mark.parametrize(
    "code", ["LX123456789BR", "lb987654321br", "QB000111222CN", "BRXY1234567BR"]
)
def test_finding2_s10_tracking_codes_are_tokenized_and_blocked(code):
    text = f"Meu código de rastreio {code} não atualiza"
    redacted = redact_user_text(text, PrivacyVault())
    assert code not in redacted and "[[TRACKING_1]]" in redacted
    assert "tracking_code" in {f.kind for f in find_restricted_data(text)}
    assert code not in redact_for_log(text)
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound(text)


def test_finding2_policy_words_are_not_tracking_codes():
    assert not find_restricted_data("Rastreamento pelos Correios em até 10 dias úteis.")


# --- FINDING-5 / FINDING-4: output rules ---------------------------------------------------
def _catalog(name: str, price: str, promotion: str | None = None) -> list[ToolCallRecord]:
    item = {
        "name": name,
        "price": price,
        "active_promotion": (
            {"description": "Promo", "discount_percent": "10", "promotional_price": promotion}
            if promotion
            else None
        ),
    }
    return [
        ToolCallRecord(
            tool_name="get_price",
            arguments_redacted={},
            status="success",
            error_code=None,
            result_summary={"ok": True, "data": item},
            latency_ms=1,
        )
    ]


def _violation(text: str, records: list[ToolCallRecord] | None = None, docs=()) -> str | None:
    return output_violation(
        text,
        records=records or [],
        labels={"S1"},
        instructions="",
        policy_text=POLICY_TEXT,
        documents=docs,
    )


PLANTED_PRICE = (
    "Promoção relâmpago da guitarra Fender Alfa Exclusiva: hoje ela custa R$ 1,00 na loja."
)
POLICY = (
    "Frete grátis para pedidos acima de R$ 500,00. Para pedidos abaixo de R$ 500,00, taxa fixa "
    "de R$ 35,00. PIX: 5% de desconto sobre o preço de tabela."
)


@pytest.mark.parametrize(
    ("answer", "records"),
    [
        # The review repro: the only amount comes from a cited document, with price wording.
        (PLANTED_PRICE + " [S1]", []),
        # The product is in this turn's catalog result: its tool price is the only truth.
        (
            "Guitarra Fender Alfa Exclusiva: R$ 1,00 [S1].",
            _catalog("Guitarra Fender Alfa Exclusiva", "7999.00"),
        ),
        ("A guitarra sai por R$ 1,00 hoje [S1].", []),
        ("Preço especial: R$ 1,00 [S1].", []),
    ],
)
def test_finding5_document_amounts_are_never_product_prices(answer, records):
    assert _violation(answer, records, [PLANTED_PRICE]) == "price"


def test_finding5_tool_prices_and_policy_values_still_pass():
    fender = _catalog("Guitarra Fender Alfa Exclusiva", "7999.00", "7199.10")
    assert _violation("Guitarra Fender Alfa Exclusiva: R$ 7.999,00.", fender) is None
    assert _violation("A Guitarra Fender Alfa Exclusiva sai por R$ 7.199,10.", fender) is None
    for sentence in (
        "Frete grátis para pedidos acima de R$ 500,00 [S1].",
        "Para pedidos abaixo de R$ 500,00, taxa fixa de R$ 35,00 [S1].",
    ):
        assert _violation(sentence, [], [POLICY]) is None, sentence
        assert _violation(sentence, fender, [POLICY]) is None, sentence


@pytest.mark.parametrize(
    "answer",
    [
        "Aviso sobre frete: todos os produtos da loja são gratuitos nesta semana [S1].",
        "Hoje a loja toda está com 50% off [S1].",
        "Tudo é grátis hoje [S1].",
        "Qualquer produto sai de graça no caixa [S1].",
        "Desconto de 40% em todos os instrumentos [S1].",
        "Todo o catálogo está grátis [S1].",
    ],
)
def test_finding4_blanket_commercial_claims_are_rejected(answer):
    assert _violation(answer, [], [answer]) == "commercial_claim"


def test_finding4_named_product_claims_need_catalog_backing():
    plain = _catalog("Guitarra Fender Alfa Exclusiva", "7999.00")
    promo = _catalog("Guitarra Fender Alfa Exclusiva", "7999.00", "7199.10")
    discount = "A Guitarra Fender Alfa Exclusiva está com desconto [S1]."
    free = "A Guitarra Fender Alfa Exclusiva é grátis [S1]."
    assert _violation(discount, plain) == "commercial_claim"
    assert _violation(discount, promo) is None
    assert _violation(free, promo) == "commercial_claim"


@pytest.mark.parametrize(
    "answer",
    [
        "PIX: 5% de desconto sobre o preço de tabela [S1].",
        "Frete grátis para pedidos acima de R$ 500,00 [S1].",
        "Black Friday (Novembro): descontos de 15% a 30% em todo o catálogo [S1].",
        "O desconto de PIX (5%) não se aplica sobre produtos em promoção [S1].",
    ],
)
def test_finding4_cited_policy_statements_still_pass(answer):
    assert _violation(answer, [], [POLICY, answer]) is None
