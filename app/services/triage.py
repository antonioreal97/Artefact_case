"""Opt-in deterministic routing; state contains only steps and existing vault tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agents.customer_service import _serialize
from app.services.privacy import fold_text
from app.services.privacy_vault import TOKEN_RE, PrivacyVault
from app.tools.context import ToolContext
from app.tools.orders import MSG_BAD_CONTACT, lookup_order

MENU = (
    "Como posso te ajudar?\n"
    "1. Horário e endereço da loja\n"
    "2. Status do meu pedido\n"
    "3. Trocas e devoluções\n"
    "4. Produtos, preços e estoque\n"
    "5. Falar com um atendente\n"
    "Responda com o número ou escreva sua dúvida."
)
ASK_ORDER = "Qual é o número do pedido? Depois, vou pedir o e-mail ou telefone usado na compra."
PRODUCTS = "Me conta o que você procura, como o instrumento ou modelo."


@dataclass(frozen=True)
class TriageConfig:
    menu: str
    hours: str
    returns: str

    @classmethod
    def from_extra(cls, extra: dict[str, Any]) -> TriageConfig | None:
        raw = extra.get("triage")
        if not isinstance(raw, dict) or raw.get("enabled") is not True:
            return None
        fields = [raw.get(key) for key in ("menu", "hours", "address", "phone", "email", "returns")]
        if not all(isinstance(value, str) and value.strip() for value in fields):
            return None
        return cls(
            menu=raw["menu"],
            hours=(
                f"Horário: {raw['hours']}\nEndereço: {raw['address']}\n"
                f"Telefone / WhatsApp: {raw['phone']}\nE-mail: {raw['email']}"
            ),
            returns=raw["returns"],
        )


@dataclass
class TriageReply:
    answer: str
    route: str
    state: dict[str, str] = field(default_factory=dict)
    handoff_reason: str | None = None
    grounded: bool = False


def redact_order_step(text: str, state: dict[str, str], vault: PrivacyVault) -> str:
    """Bare order numbers are meaningful only in the guided order step.

    Called on already-redacted text: phone/email spans can never become order ids.
    Also accepts a bare number and a contact together, but never numbers in questions.
    """
    if state.get("step") != "order":
        return text
    match = re.fullmatch(r"\s*#?(\d{1,10})(\s*[,;]?\s*(?:\[\[(?:EMAIL|PHONE)_\d+\]\])?)\s*", text)
    if not match:
        return text
    return vault.tokenize("ORDER", match[1]) + match[2]


def _choice(text: str, redacted: str, state: dict[str, str]) -> str | None:
    value = fold_text(text).strip(" .!?\n")
    if state.get("step") == "menu" and value in {"1", "2", "3", "4", "5"}:
        return value
    # Deliberately narrow shortcuts. Questions with qualifications remain agent work.
    if re.fullmatch(
        r"(?:(?:qual (?:e |o |e o )?|qual o |onde fica )?(?:o |a )?)"
        r"(?:horario|endereco|telefone|whatsapp|e-mail|contato)(?: da loja| de funcionamento)?",
        value,
    ) or value in {"onde fica a loja", "qual o horario da loja", "qual o endereco da loja"}:
        return "1"
    if re.fullmatch(
        r"(?:(?:qual (?:e )?o |consultar |ver |quero saber o )?"
        r"(?:status|rastreamento|andamento) (?:do |de )?(?:meu )?pedido|(?:meu )?pedido)",
        value,
    ) or (
        re.search(r"\[\[ORDER_\d+\]\]", redacted)
        and re.search(r"\b(status|rastreamento|andamento)\b", value)
    ):
        return "2"
    if value in {
        "trocas",
        "devolucoes",
        "trocas e devolucoes",
        "politica de trocas",
        "quero devolver",
    }:
        return "3"
    if value in {"produtos", "precos", "estoque", "produtos, precos e estoque"}:
        return "4"
    if value in {"atendente", "humano", "falar com um atendente"}:
        return "5"
    return None


async def route_triage(
    config: TriageConfig,
    state: dict[str, str],
    *,
    text: str,
    redacted: str,
    ctx: ToolContext,
) -> TriageReply | None:
    value = fold_text(text).strip(" .!?\n")
    if value in {"menu", "0", "cancelar", "cancela"}:
        return TriageReply(config.menu, "triage:menu", {"step": "menu"})
    if state.get("step") == "agent_next":
        return None
    choice = _choice(text, redacted, state)
    if choice == "1":
        return TriageReply(config.hours, "triage:hours")
    if choice == "3":
        return TriageReply(config.returns, "triage:returns", {"step": "agent_next"})
    if choice == "4":
        return TriageReply(PRODUCTS, "triage:products", {"step": "agent_next"})
    if choice == "5":
        return TriageReply("", "triage:human", handoff_reason="customer_request")
    if choice != "2" and state.get("step") != "order":
        return None
    orders = [m.group() for m in TOKEN_RE.finditer(redacted) if m[1] == "ORDER"]
    contacts = [m.group() for m in TOKEN_RE.finditer(redacted) if m[1] in {"EMAIL", "PHONE"}]
    if choice != "2":
        residue = TOKEN_RE.sub("", redacted)
        residue = re.sub(
            r"\b(?:pedido|numero|meu|o|e|email|e-mail|telefone|contato|da|compra|usado|no|na|eh)\b",
            "",
            fold_text(residue),
        ).strip(" .,;:#?!\n")
        # A different question cancels the guide, even when it includes a contact.
        if residue or not (orders or contacts):
            return None
    order = orders[0] if len(orders) == 1 else state.get("order_token")
    if not order or ctx.vault.resolve(order) is None:
        return TriageReply(ASK_ORDER, "triage:order", {"step": "order"})
    pending = {"step": "order", "order_token": order}
    if len(contacts) != 1:
        return TriageReply(MSG_BAD_CONTACT, "triage:order", pending)
    result = await lookup_order(ctx, order, contacts[0])
    # Same outbound scan and grounding evidence as the SDK adapter; never raw data.
    _serialize(result, ctx, "lookup_order", [])
    if not result.ok or result.data is None:
        assert result.error is not None
        return TriageReply(result.error.message, "triage:order", pending)
    data = result.data
    status = {
        "pending": "pendente",
        "confirmed": "confirmado",
        "shipped": "enviado",
        "delivered": "entregue",
        "cancelled": "cancelado",
        "unknown": "não informada",
    }[data.status]
    lines = [f"Situação: {status}."]
    if data.tracking_token:
        lines.append(f"Rastreamento: {data.tracking_token}")
    if data.estimated_delivery_token:
        lines.append(f"Previsão de entrega: {data.estimated_delivery_token}")
    return TriageReply("\n".join(lines), "triage:order", grounded=True)
