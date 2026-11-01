"""Offline reproductions of native-model failures; no provider network calls."""

from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest
from agents import ModelSettings
from agents.models.interface import ModelTracing
from agents.testing import ScriptedModel, assistant_message, function_call

from app.agents.guardrails import output_violation
from app.core.config import Settings
from app.models import CommerceProduct
from app.services.attempt_limiter import InMemoryAttemptLimiter
from app.services.chat import ChatService
from app.services.model_gateway import GuardedModelProvider, PrivacyGuardedModel
from app.services.privacy import PrivacyBoundaryError, scan_outbound
from app.services.vault_store import InMemoryVaultStore
from app.tools.context import ToolCallRecord

OPAQUE_UUID = "49191887-aaaa-bbbb-cccc-283445580000"
DIGITS = ["49191887", "(67) 3341-8888"]


@pytest.mark.parametrize("opaque", DIGITS)
async def test_provider_metadata_passes_unchanged_without_logging(opaque, caplog):
    inner = ScriptedModel([[assistant_message("ok")]])
    items = [
        {
            "type": "reasoning",
            "id": opaque,
            "summary": [],
            "content": [],
            "encrypted_content": opaque,
        },
        {
            "type": "function_call",
            "id": opaque,
            "call_id": opaque,
            "name": "search_products",
            "arguments": '{"query":"violão"}',
            "status": opaque,
        },
        {"type": "function_call_output", "id": opaque, "call_id": opaque, "output": "{}"},
        {
            "type": "message",
            "role": "assistant",
            "id": opaque,
            "status": opaque,
            "content": [{"type": "output_text", "text": "Olá"}],
        },
    ]
    original = copy.deepcopy(items)
    await PrivacyGuardedModel(inner).get_response(
        "Política",
        items,
        ModelSettings(),
        [],
        None,
        [],
        ModelTracing.ENABLED,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    )
    assert inner.calls[0].input == original == items
    assert inner.calls[0].tracing == ModelTracing.ENABLED_WITHOUT_DATA
    assert opaque not in caplog.text


@pytest.mark.parametrize("digits", DIGITS)
@pytest.mark.parametrize(
    "location", ["arguments", "output", "message", "summary", "content", "instructions"]
)
def test_customer_text_is_scanned_in_every_item(location, digits):
    item = {"type": "reasoning", "id": "safe", "summary": []}
    payload = {"input": [item], "instructions": "Política"}
    if location in {"arguments", "output"}:
        item.update(type="function_call" if location == "arguments" else "function_call_output")
        item[location] = json.dumps({"text": digits})
    elif location == "message":
        item.update(
            type="message", role="assistant", content=[{"type": "output_text", "text": digits}]
        )
    elif location == "instructions":
        payload[location] = digits
    else:
        item[location] = [{"type": "summary_text", "text": digits}]
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound(payload)


@pytest.mark.parametrize(
    "key", ["conversation_id", "message_id", "handoff_id", "document_id", "chunk_id"]
)
def test_uuid_schema_fields_pass_but_free_text_does_not(key):
    data = {key: OPAQUE_UUID, "summary": "Consulta concluída"}
    scan_outbound({"ok": True, "data": data})
    scan_outbound(
        {"input": [{"type": "function_call_output", "call_id": "x", "output": json.dumps(data)}]}
    )
    for value in [
        OPAQUE_UUID,
        json.dumps(data),
        json.dumps({"type": "reasoning", "id": OPAQUE_UUID}),
    ]:
        with pytest.raises(PrivacyBoundaryError):
            scan_outbound({"input": [{"role": "user", "content": value}]})
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound({key: "49191887"})


@pytest.mark.parametrize("key", ["order_id", "customer_id", "description", "text", "id"])
def test_other_fields_do_not_receive_uuid_exemption(key):
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound({"ok": True, "data": {key: OPAQUE_UUID}})


def catalog(*available: bool) -> list[ToolCallRecord]:
    return [
        ToolCallRecord(
            tool_name="search_products",
            arguments_redacted={},
            status="success",
            error_code=None,
            result_summary={
                "data": {
                    "items": [
                        {
                            "name": name,
                            "price": "800.00",
                            "available": stock,
                            "stock_quantity": 4 if stock else 0,
                        }
                        for name, stock in zip(
                            ["Yamaha C40", "Yamaha F310"], available, strict=False
                        )
                    ]
                }
            },
            latency_ms=0,
        )
    ]


def violation(answer, *, records=None, current_message=""):
    return output_violation(
        answer,
        records=records or [],
        labels=set(),
        instructions="",
        policy_text="",
        current_message=current_message,
    )


@pytest.mark.parametrize("budget", ["R$1000", "R$ 1.000", "1000 reais", "R$ 1.000,00"])
def test_current_budget_echo_with_real_product_prices(budget):
    assert (
        violation(
            "Violões disponíveis (até R$1.000): Yamaha C40 por R$ 800,00.",
            records=catalog(True),
            current_message=f"Quais violões custam até {budget}?",
        )
        is None
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Yamaha C40 custa R$ 1.000.",
        "Yamaha C40: R$ 1.000.",
        "Yamaha C40 custa até R$ 1.000.",
        "Custa R$ 1.000.",
        "Disponíveis até R$ 1.000: Yamaha C40 por R$ 1.000.",
    ],
)
def test_budget_never_authorizes_wrong_product_price(answer):
    assert violation(answer, records=catalog(True), current_message="Tenho R$1000") == "price"


def test_prior_budget_cannot_be_echoed_without_current_message():
    assert violation("Violões até R$1.000: Yamaha C40 por R$800.", records=catalog(True)) == "price"


@pytest.mark.parametrize(
    ("stocks", "answer", "expected"),
    [
        ((True, True), "Temos vários violões Yamaha em estoque.", None),
        ((True, False), "Temos vários violões Yamaha em estoque.", "stock"),
        ((False, False), "Estão indisponíveis.", None),
        ((False, True), "Estão indisponíveis.", "stock"),
        ((True, True), "Disponíveis, com 4 unidades.", "stock"),
        ((False, False), "Disponíveis, com 99 unidades.", "stock"),
    ],
)
def test_generic_availability_needs_unanimous_evidence(stocks, answer, expected):
    assert violation(answer, records=catalog(*stocks)) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "Você quer dizer bateria acústica ou eletrônica? "
        "Quer que eu pesquise só itens em estoque aqui no Empório da Música?",
        "Quer o Yamaha C40 em estoque?",
        "Posso pesquisar itens em estoque para você.",
    ],
)
def test_stock_questions_and_search_offers_are_not_claims(answer):
    assert violation(answer) is None
    assert violation(answer, records=catalog(False)) is None


def test_question_does_not_hide_neighboring_stock_claim():
    assert violation("Quer que eu pesquise? Temos violões em estoque.") == "stock"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("answer", "expected_handoff"),
    [
        ("Violões disponíveis (até R$1.000): Yamaha C40 por R$800.", False),
        ("Yamaha C40 custa R$1.000.", True),
    ],
)
async def test_budget_guardrail_chat_call_site(
    session_factory, two_tenants, answer, expected_handoff
):
    tenant, _ = two_tenants
    async with session_factory() as session:
        session.add(
            CommerceProduct(
                tenant_id=tenant,
                external_id=1,
                category_external_id=1,
                name="Yamaha C40",
                price=Decimal("800"),
                stock_quantity=4,
                status="active",
            )
        )
        await session.commit()
    model = ScriptedModel(
        [
            [function_call("search_products", {"query": "Yamaha"}, call_id="call_49191887")],
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
        result = await service.handle_message(
            session,
            tenant_id=tenant,
            conversation_id=None,
            text="Quais violões disponíveis até R$1000?",
            correlation_id="budget-test",
        )
    assert bool(result.handoff) is expected_handoff
    assert result.grounded is not expected_handoff
    if not expected_handoff:
        assert result.reply == answer


@pytest.mark.parametrize("field", ["content", "arguments", "summary"])
def test_json_text_cannot_impersonate_provider_metadata(field):
    spoof = json.dumps({"type": "reasoning", "id": "49191887", "encrypted_content": "49191887"})
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound({"input": [{"type": "function_call", field: spoof}]})


def test_nested_text_in_tool_result_cannot_impersonate_schema_id():
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound(
            {"ok": True, "data": {"description": json.dumps({"message_id": OPAQUE_UUID})}}
        )


def test_compaction_opaque_data_does_not_skip_text():
    item = {"type": "compaction", "id": "49191887", "encrypted_content": "49191887"}
    scan_outbound({"input": [item]})
    item["content"] = "49191887"
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound({"input": [item]})
