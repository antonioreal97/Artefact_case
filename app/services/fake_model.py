"""Deterministic NON-PRODUCTION model for offline demos and integration tests.

This is a small scripted tool router, not an LLM and not a general language interpreter.
It renders only successful tool outputs, never treats documents as executable commands,
and deliberately refuses suspicious document passages. Production creation is forbidden
by Settings and build_model_provider. No network calls are made here.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import AsyncIterator
from decimal import Decimal

from agents import ModelSettings
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import (
    ModelResponse,
    TResponseInputItem,
    TResponseOutputItem,
    TResponseStreamEvent,
)
from agents.models.interface import ModelTracing
from agents.testing import ScriptedModel, assistant_message, function_call
from agents.tool import Tool
from openai.types.responses.response_prompt_param import ResponsePromptParam

from app.agents.guardrails import (
    NO_INFORMATION,
    OUT_OF_SCOPE,
    OUT_OF_SCOPE_EN,
    check_input,
    is_greeting_only,
)
from app.services.privacy import fold_text


def _currency(value: object) -> str:
    return f"{Decimal(str(value)):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _prefers_english(text: str) -> bool:
    """Conservative offline cue only; not a general language detector."""
    folded = fold_text(text)
    en = bool(
        re.search(
            r"\b(?:what|which|how|where|when|why|please|sorry|capital|iceland|"
            r"return|policy|order|status|guitar|stock|price|cost)\b",
            folded,
        )
    )
    pt = bool(
        re.search(
            r"\b(?:qual|quais|como|onde|quando|por\s*que|pedido|politica|"
            r"devolucao|capital|islandia|violao|estoque|preco)\b",
            folded,
        )
    )
    return en and not pt


_COMMERCE = re.compile(r"(?i)pre[cç]o|valor|custa|estoque|dispon[ií]ve(?:l|is)|price|cost|stock")
# Service inquiries concern store policy, even when they name an instrument or price.
_SERVICE = re.compile(
    r"(?i)\b(?:servi[cç]os?|manuten[cç][aã]o|consertos?|reparos?|"
    r"regulage(?:m|ns)|luthier(?:ia)?)\b"
)
_PRODUCT = re.compile(r"(?i)produto|viol(?:[aã]o|[oõ]es)|guitarra|guitar|piano|teclado")
_IN_STOCK = re.compile(r"(?i)\bdispon[ií]ve(?:l|is)\b|\bem estoque\b|\bin stock\b")
_MAX_PRICE = re.compile(
    r"(?i)\b(?:at[eé]|abaixo de|no m[aá]ximo|up to|under|below|at most)\s*(?:R\$\s*)?"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|"
    r"\d+(?:[.,]\d{1,2})?)"
    r"(?:\s*reais)?"
)
_POLICY = re.compile(
    r"(?i)troca|devolu|defeito|garantia|desconto|pix|frete|prazo|entrega|parcel|pagamento|"
    r"hor[aá]rio|pol[ií]tica|promo[cç]|return|refund|shipping|warranty|policy"
)
# Conservative offline routing only; this is not a general intent classifier.
_STORE = re.compile(
    r"\b(?:loja|voces|compra\w*|pedido\w*|catalogo|instrumento\w*|atendimento|"
    r"endereco|contato|yamaha|takamine|tagima|cordas?|palhetas?|bateria\w*|ukulele|"
    r"store|shop|product\w*|price\w*|stock|order\w*|return\w*|shipping|guitar\w*)\b"
)
# Recoverable errors whose fixed tool message asks the customer for corrected data.
_RECOVERABLE = frozenset({"invalid_input", "not_authorized", "not_found"})


def _catalog_arguments(text: str) -> dict[str, object]:
    """Small Portuguese filter grammar for the offline demo, not general NLU."""
    # Keep a greeting prefix out of catalog matching without dropping request words.
    words = text.split()
    for end in range(len(words) - 1, 0, -1):
        if is_greeting_only(" ".join(words[:end])):
            text = " ".join(words[end:])
            break
    arguments: dict[str, object] = {"in_stock_only": bool(_IN_STOCK.search(text))}
    maximum = _MAX_PRICE.search(text)
    if maximum:
        raw = maximum.group(1)
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", raw):
            raw = raw.replace(",", "")
        elif "," in raw or re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
            raw = raw.replace(".", "").replace(",", ".")
        arguments["max_price"] = float(Decimal(raw))
    query = _MAX_PRICE.sub(" ", text)
    query = re.sub(r"(?i)\bviol[oõ]es\b", "violão", query)
    query = re.sub(r"(?i)\bacoustic guitars?\b", "violão", query)
    query = re.sub(
        r"\b(?:qual|quais|op[cç][oõ]es|quanto|o|a|os|as|um|uma|do|da|de|tem|voc[eê]s|"
        r"pre[cç]o|valor|custa(?:ndo)?|estoque|dispon[ií]ve(?:l|is)|produto|por|favor|em|[eé]|"
        r"which|what|how|much|many|do|you|have|for|the|in|stock|please|hi|hello)\b",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"[^\w\s-]", " ", query)
    arguments["query"] = " ".join(query.split()) or None
    return arguments


class FakeCommerceModel(ScriptedModel):
    """Uses the SDK's inspected scripted-model implementation, including streaming."""

    def __init__(self) -> None:
        super().__init__()
        self._sequence = 0

    def _call(self, name: str, arguments: dict[str, object]) -> list[TResponseOutputItem]:
        self._sequence += 1
        return [function_call(name, arguments, call_id=f"offline-call-{self._sequence}")]

    def _step(self, input: str | list[TResponseInputItem]) -> list[TResponseOutputItem]:
        items = [{"role": "user", "content": input}] if isinstance(input, str) else list(input)
        last_user = max(
            (i for i, item in enumerate(items) if item.get("role") == "user"), default=-1
        )
        text = str(items[last_user].get("content", "")) if last_user >= 0 else ""
        turn = items[last_user + 1 :]
        calls = {
            str(item.get("call_id")): str(item.get("name"))
            for item in turn
            if item.get("type") == "function_call"
        }
        outputs: list[tuple[str, dict[str, object]]] = []
        for item in turn:
            if item.get("type") == "function_call_output":
                try:
                    parsed = json.loads(str(item.get("output", "")))
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    outputs.append((calls.get(str(item.get("call_id")), ""), parsed))
        if not outputs:
            order = re.search(r"\[\[ORDER_\d+\]\]", text)
            contact = re.search(r"\[\[(?:EMAIL|PHONE)_\d+\]\]", text)
            if order and contact:
                return self._call(
                    "lookup_order",
                    {
                        "order_token": order.group(),
                        "contact_token": contact.group(),
                    },
                )
            decision = check_input(text)
            if decision.reason:
                return self._call(
                    "request_human_handoff",
                    {
                        "reason": decision.reason,
                        "summary": "Solicitação de atendimento humano.",
                    },
                )
            if is_greeting_only(text):
                return [assistant_message("Olá! Como posso ajudar?")]
            if not _SERVICE.search(text) and (
                _COMMERCE.search(text) or (_PRODUCT.search(text) and not _POLICY.search(text))
            ):
                return self._call("search_products", _catalog_arguments(text))
            # Hash retrieval needs shared lexemes; normalize common return paraphrases.
            # The store manual is Portuguese — map English policy cues before retrieval.
            query = text
            if _prefers_english(text) and _POLICY.search(text):
                query = (
                    "política de devolução direito de arrependimento compra online "
                    "trocas e devoluções"
                )
            query = re.sub(r"(?i)\barrependi\b", "arrependimento", query)
            query = re.sub(r"(?i)\bdevolver\b", "devolução", query)
            return self._call("search_knowledge", {"query": query, "category": None})
        if re.search(r"estoque|dispon[ií]vel", text, re.IGNORECASE):
            for name, envelope in outputs:
                data = envelope.get("data")
                if isinstance(data, dict):
                    data = data.get("items")
                if name == "search_products" and envelope.get("ok") and isinstance(data, list):
                    if (
                        len(data) == 1
                        and isinstance(data[0], dict)
                        and not any(n == "check_stock" for n, _ in outputs)
                    ):
                        return self._call("check_stock", {"product_id": data[0]["product_id"]})
        parts: list[str] = []
        for name, envelope in outputs:
            if not envelope.get("ok"):
                error = envelope.get("error")
                if (
                    name == "lookup_order"
                    and isinstance(error, dict)
                    and error.get("code") in _RECOVERABLE
                ):
                    parts.append(str(error.get("message", "")))
                continue
            data = envelope.get("data")
            if name == "search_knowledge" and isinstance(data, dict) and data.get("sufficient"):
                sources = data.get("sources", [])
                if isinstance(sources, list):
                    # Bound the two demo intents to their relevant returned section;
                    # the text and citation still come exclusively from the tool.
                    topic = fold_text(text)
                    section = (
                        "arrependimento"
                        if re.search(r"arrepend", topic)
                        else "dados da empresa"
                        if "endereco" in topic
                        else None
                    )
                    if section:
                        relevant = [
                            source
                            for source in sources
                            if isinstance(source, dict)
                            and section in fold_text(str(source.get("content", "")))
                        ]
                        if relevant:
                            sources = relevant
                    for source in sources:
                        if not isinstance(source, dict):
                            continue
                        content = html.unescape(
                            re.sub(r"</?untrusted_document[^>]*>", "", str(source["content"]))
                        )
                        # Refuse malicious passages; never execute/echo injected instructions.
                        if not check_input(content).refusal:
                            parts.append(f"{content.strip()} [{source['label']}]")
            elif name in {"search_products", "get_product_details", "get_price"}:
                if isinstance(data, dict) and "items" in data:
                    data = data["items"]
                products = data if isinstance(data, list) else [data]
                for product in products:
                    if isinstance(product, dict) and "price" in product:
                        parts.append(
                            f"{product.get('name', 'Produto')}: R$ {_currency(product['price'])}."
                        )
            elif name == "check_stock" and isinstance(data, dict):
                parts.append(
                    f"{data.get('name', 'Produto')}: "
                    + ("disponível em estoque." if data.get("available") else "indisponível.")
                )
            elif (
                name == "lookup_order" and isinstance(data, dict) and data.get("identity_verified")
            ):
                status = str(data.get("order_status", data.get("status", "unknown")))
                translated = {
                    "pending": "pendente",
                    "confirmed": "confirmado",
                    "shipped": "enviado",
                    "delivered": "entregue",
                    "cancelled": "cancelado",
                    "unknown": "não informada",
                }.get(status, "não informada")
                parts.append(f"Situação: {translated}.")
                for key, value in data.items():
                    if isinstance(value, str) and re.fullmatch(
                        r"\[\[(?:TRACKING|DATE)_\d+\]\]", value
                    ):
                        label = "Rastreamento" if key == "tracking_token" else "Previsão de entrega"
                        parts.append(f"{label}: {value}")
            elif name == "request_human_handoff":
                parts.append("Solicitei atendimento humano para ajudar com sua dúvida.")
        store_related = any(
            pattern.search(text) for pattern in (_COMMERCE, _SERVICE, _PRODUCT, _POLICY)
        ) or bool(_STORE.search(fold_text(text)))
        if parts:
            return [assistant_message("\n".join(parts))]
        if store_related:
            fallback = NO_INFORMATION
        else:
            fallback = OUT_OF_SCOPE_EN if _prefers_english(text) else OUT_OF_SCOPE
        return [assistant_message(fallback)]

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        self.enqueue(self._step(input))
        return await super().get_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        self.enqueue(self._step(input))
        async for event in super().stream_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        ):
            yield event
