"""Constants and helpers shared by the adversarial suite (imported by conftest and tests).

Every restricted value below is synthetic. Tests assert these values never reach the
model, logs or redacted DB columns, and never cross tenants."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from agents import Model
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from app.services.fake_model import FakeCommerceModel
from app.services.privacy_vault import TOKEN_RE


@dataclass(frozen=True)
class TenantData:
    customer_name: str
    email: str
    phone: str
    phone_digits: str
    order_id: int
    tracking: str
    delivery: date
    product_name: str
    product_price_text: str  # as rendered by the fake model, e.g. "7.999,00"
    marker: str  # unique token inside this tenant's private document
    private_doc: str


A_DATA = TenantData(
    customer_name="Ana Pessoa Teste",
    email="ana.teste@example.test",
    phone="(11) 99999-8888",
    phone_digits="11999998888",
    order_id=481516,
    tracking="BRAA1234567BR",
    delivery=date(2026, 11, 20),
    product_name="Guitarra Fender Alfa Exclusiva",
    product_price_text="7.999,00",
    marker="ALFA-7731",
    private_doc=(
        "Garantia estendida: o código interno da garantia estendida é ALFA-7731 e cobre "
        "cordas e trastes por vinte e quatro meses."
    ),
)
B_DATA = TenantData(
    customer_name="Bruno Sintético Souza",
    email="bruno@lojasintetica.test",
    phone="(21) 98888-7777",
    phone_digits="21988887777",
    order_id=900417,
    tracking="BRBB7654321BR",
    delivery=date(2026, 12, 2),
    product_name="Teclado Bravo Sintético",
    product_price_text="1.500,00",
    marker="BRAVO-4410",
    private_doc=(
        "Garantia estendida: o código interno da garantia estendida é BRAVO-4410 e cobre "
        "teclas e pedais por seis meses."
    ),
)
SHARED_DOC = (
    "Horário de funcionamento da loja: segunda a sábado das nove às dezoito horas. "
    "Aos domingos a loja permanece fechada."
)
CPF = "123.456.789-09"
MAX_UPLOAD = 512 * 1024  # route limit configured for the adversarial app


def restricted_values(data: TenantData) -> list[str]:
    """Raw values that must never reach the model, logs or redacted columns."""
    return [
        data.customer_name,
        data.email,
        data.phone,
        data.phone_digits,
        str(data.order_id),
        data.tracking,
        data.delivery.isoformat(),
        data.delivery.strftime("%d/%m/%Y"),
    ]


def leaked(blob: str, values: Iterable[str]) -> list[str]:
    folded = blob.casefold()
    return [value for value in values if value.casefold() in folded]


@dataclass
class TenantWorld:
    id: uuid.UUID
    slug: str
    key: str
    data: TenantData
    private_doc_id: uuid.UUID
    shared_doc_id: uuid.UUID
    shared_version_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.key}


@dataclass
class World:
    a: TenantWorld
    b: TenantWorld


ModelFactory = Callable[[], Model]


@dataclass
class ModelControl:
    """Builds the model for each turn and keeps every instance to inspect egress payloads."""

    factory: ModelFactory = FakeCommerceModel
    models: list[Any] = field(default_factory=list)

    def new(self) -> Model:
        model = self.factory()
        self.models.append(model)
        return model

    @property
    def calls(self) -> list[Any]:
        return [call for model in self.models for call in getattr(model, "calls", [])]

    def payloads(self) -> str:
        """Everything that crossed the model boundary (instructions, history, tool output)."""
        return self.payloads_since(0)

    def inputs_since(self, index: int) -> str:
        """Only the conversation/tool items (the system prompt legitimately names tokens)."""
        return json.dumps(
            [call.input for call in self.calls[index:]], ensure_ascii=False, default=str
        )

    def payloads_since(self, index: int) -> str:
        return json.dumps(
            [(call.system_instructions, call.input) for call in self.calls[index:]],
            ensure_ascii=False,
            default=str,
        )


def scripted(*steps: Sequence[Any] | ModelStep) -> ModelFactory:
    """Factory producing a fresh ScriptedModel (one per turn) from the same steps."""

    def make() -> Model:
        return ScriptedModel(list(steps))

    return make


def history_lookup_model() -> Model:
    """A model that uses tokens from the WHOLE history (not only the last message) to call
    lookup_order, then repeats the TRACKING/DATE tokens returned by the tool."""

    def first(call: Any) -> list[Any]:
        blob = json.dumps(call.input, ensure_ascii=False)
        order = re.search(r"\[\[ORDER_\d+\]\]", blob)
        contact = re.search(r"\[\[(?:EMAIL|PHONE)_\d+\]\]", blob)
        if not (order and contact):
            return [assistant_message("Não encontrei informação.")]
        return [
            function_call(
                "lookup_order",
                {"order_token": order.group(), "contact_token": contact.group()},
                call_id="history-lookup-1",
            )
        ]

    def second(call: Any) -> list[Any]:
        outputs = [
            item
            for item in call.input
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        envelope = json.loads(str(outputs[-1]["output"])) if outputs else {}
        data = envelope.get("data") or {}
        tokens = [
            v
            for v in data.values()
            if isinstance(v, str)
            and TOKEN_RE.fullmatch(v)
            and v.startswith(("[[TRACKING", "[[DATE"))
        ]
        status = data.get("status", "desconhecida")
        return [assistant_message(f"Situação: {status}. " + " ".join(tokens))]

    return ScriptedModel([ModelStep.respond(first), ModelStep.respond(second)])


async def chat(
    client: httpx.AsyncClient,
    tenant: TenantWorld,
    text: str,
    conversation_id: str | None = None,
    *,
    expect: int = 200,
) -> dict[str, Any]:
    body: dict[str, Any] = {"message": text}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    response = await client.post("/v1/chat/messages", headers=tenant.headers, json=body)
    assert response.status_code == expect, response.text
    result: dict[str, Any] = response.json()
    return result


_INTERNALS = (
    "traceback",
    'file "',
    ".py",
    "sqlalchemy",
    "asyncpg",
    "psycopg",
    "pypdf",
    "zipfile",
    "exception",
    "redis://",
    "postgresql",
    "select ",
    "insert into",
)


def assert_safe_error(response: httpx.Response) -> None:
    """Error bodies use the documented envelope and expose no internals."""
    body = response.json()
    assert isinstance(body, dict), body
    assert set(body) <= {"error", "detail", "correlation_id"}, body
    lowered = response.text.casefold()
    for needle in _INTERNALS:
        assert needle not in lowered, (needle, response.text)
