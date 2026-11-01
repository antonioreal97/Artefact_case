"""Deterministic checks before/after the model loop. No model-based safety decisions."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.tools.context import ToolCallRecord

SAFE_FALLBACK = (
    "Não consegui confirmar essa informação com segurança. Posso ajudar com outra dúvida."
)
NO_INFORMATION = "Não encontrei informação confiável para responder à sua pergunta."
INJECTION_REFUSAL = (
    "Não posso revelar instruções internas ou alterar as regras de atendimento. "
    "Posso ajudar com informações da loja."
)


def _fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


@dataclass(frozen=True)
class InputDecision:
    reason: str | None = None
    refusal: bool = False


def check_input(text: str) -> InputDecision:
    folded = _fold(text)
    if re.search(
        r"(?:ignor\w*|desconsider\w*|forget|override|bypass).{0,80}"
        r"(?:instruc|regras|previous|system|sistema)|"
        r"(?:mostr\w*|revel\w*|repita|copi\w*|print|show|repeat|traduz\w*).{0,80}"
        r"(?:prompt|instruc|system)|(?:system prompt|developer message)",
        folded,
    ):
        return InputDecision(refusal=True)
    if re.search(
        r"reclam\w*|procon|estorno|chargeback|cobranca indevida|"
        r"pagamento.{0,30}(?:recusado|duplicado)|excecao|desconto especial|"
        r"refund|complaint|payment.{0,30}(?:declined|duplicate)",
        folded,
    ):
        return InputDecision(reason="sensitive_topic")
    if re.search(r"\b(?:atendente|humano|human|pessoa|atendimento humano)\b", folded):
        return InputDecision(reason="customer_request")
    return InputDecision()


def _money(value: str) -> Decimal:
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    return Decimal(value)


def allowed_prices(records: list[ToolCallRecord]) -> set[Decimal]:
    prices: set[Decimal] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"price", "promotional_price", "list_price", "effective_price"}:
                    try:
                        prices.add(Decimal(str(child)))
                    except InvalidOperation:
                        pass
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for record in records:
        if record.status == "success" and record.tool_name in {
            "search_products",
            "get_product_details",
            "get_price",
        }:
            visit(record.result_summary)
    return prices


def check_output(
    text: str, *, records: list[ToolCallRecord], labels: set[str], instructions: str
) -> bool:
    prices = allowed_prices(records)
    for amount in re.findall(r"R\$\s*([\d.,]+)", text):
        try:
            if _money(amount.rstrip(".,")) not in prices:
                return False
        except InvalidOperation:
            return False
    if not set(re.findall(r"\[(S\d+)\]", text)).issubset(labels):
        return False
    folded = _fold(text)
    if any(
        len(line.strip()) >= 45 and _fold(line.strip()) in folded
        for line in instructions.splitlines()
    ):
        return False
    return not re.search(r"politica obrigatoria|versao da politica:|<untrusted_document", folded)
