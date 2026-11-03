"""Offline regressions for the independent v10 review (E3b..E3f / D-33..D-36)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from agents.testing import ScriptedModel, assistant_message, function_call

from app.agents.guardrails import (
    OUT_OF_SCOPE_EN,
    is_out_of_scope_refusal,
    money_amounts,
    output_violation,
)
from app.agents.instructions import (
    POLICY_TEXT,
    WELCOME_ALREADY_SENT_NOTE,
    build_instructions,
)
from app.models import AgentSettings
from app.rag.embeddings import HashEmbeddingProvider
from app.rag.ingestion import create_document_version, process_document_version
from app.tools.context import ToolCallRecord
from scripts.commerce import DATA_DIR
from scripts.seed import ingest_policy
from tests.agent import test_chat
from tests.agent.test_chat import make_service, turn

commerce = test_chat.commerce


def _handoff(status: str = "success") -> ToolCallRecord:
    return ToolCallRecord(
        tool_name="request_human_handoff",
        arguments_redacted={"reason": "customer_request"},
        status=status,
        error_code=None if status == "success" else "unavailable",
        result_summary={"ok": status == "success"},
        latency_ms=1,
    )


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


HANDOFF_SENTENCE = "Passei sua solicitação pra um atendente te ajudar."


@pytest.mark.parametrize(
    ("answer", "records", "expected"),
    [
        (HANDOFF_SENTENCE, [], "success_claim"),
        (HANDOFF_SENTENCE, [_handoff("error")], "success_claim"),
        (HANDOFF_SENTENCE, [_handoff("success")], None),
        ("Passei sua solicitação para um atendente.", [], "success_claim"),
        ("Já encaminhei sua solicitação.", [], "success_claim"),
    ],
)
def test_new_handoff_sentence_needs_successful_tool(answer, records, expected):
    assert (
        output_violation(
            answer,
            records=records,
            labels=set(),
            instructions="",
            policy_text="",
        )
        == expected
    )


def test_welcome_note_leak_is_rejected_against_effective_instructions():
    settings = AgentSettings(
        tenant_id=uuid.uuid4(),
        assistant_name="Assistente",
        business_description="loja",
        tone_of_voice="acolhedor",
        default_language="pt-BR",
        chat_model="fake",
        extra={"tagline": "Sua música começa aqui."},
    )
    instructions = build_instructions(settings, welcome_already_sent=True)
    note_line = WELCOME_ALREADY_SENT_NOTE.splitlines()[0]
    assert len(note_line) >= 45
    assert "horário de atendimento" not in WELCOME_ALREADY_SENT_NOTE.lower()
    assert "boas-vindas já enviadas" in note_line.lower()
    assert (
        output_violation(
            note_line,
            records=[],
            labels=set(),
            instructions=instructions,
            policy_text=POLICY_TEXT,
        )
        == "prompt_leak"
    )
    assert (
        output_violation(
            note_line,
            records=[],
            labels=set(),
            instructions=build_instructions(settings),
            policy_text=POLICY_TEXT,
        )
        is None
    )


def test_mandatory_privacy_clause_survives():
    assert "Não tente resolver tokens, identificar clientes ou expor dados pessoais." in POLICY_TEXT
    assert "Nunca peça CEP, endereço, CPF ou documentos" in POLICY_TEXT


@pytest.mark.parametrize(
    "answer",
    [
        "Não posso calcular o frete por aqui. O valor aparece no checkout. [S1]",
        "Você pode consultar o frete no checkout. [S1]",
        "Para iniciar o pedido de devolução, posso te passar para um atendente.",
        "Um atendente pode calcular o frete para você.",
        "O frete é calculado no checkout. [S1]",
        # V3c: safe guidance the removed offer filter rejected.
        "Posso explicar como você pode calcular o frete no checkout. [S1]",
        "Posso te orientar a iniciar sua devolução pelo atendimento. [S1]",
    ],
)
def test_safe_freight_and_referral_answers_pass(answer):
    """No output offer filter (D-35): offers are prompt-only, so guidance is never rejected."""
    assert (
        output_violation(
            answer,
            records=[],
            labels={"S1"},
            instructions="",
            policy_text="",
        )
        is None
    )


def test_cited_return_policy_without_offer_passes():
    answer = (
        "Sim, você pode devolver por arrependimento em até 7 dias corridos após o recebimento. [S1]"
    )
    assert (
        output_violation(
            answer,
            records=[],
            labels={"S1"},
            instructions="",
            policy_text="",
        )
        is None
    )


@pytest.mark.parametrize(
    "reply",
    [
        OUT_OF_SCOPE_EN,
        OUT_OF_SCOPE_EN.replace("policies.", "policies!"),
        "I can only help with store topics: products, prices, stock, orders, and our policies!",
    ],
)
def test_english_colon_list_scope_punctuation(reply):
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


def test_english_scope_exclamation_does_not_exempt_appended_claim():
    answer = OUT_OF_SCOPE_EN.replace("policies.", "policies!") + " The C40 costs R$100."
    assert not is_out_of_scope_refusal(answer)


WORD_PRICE_LIMITATION = (
    "Documented limitation (D-36): money validation is main's digit-only parser, so an "
    "amount written only in number words is invisible to the price check."
)


def test_number_words_are_not_parsed_as_amounts():
    """Main's behavior: only digits are amounts; number-word budgets are not read."""
    for text in ["tem violão até mil reais?", "até dois mil reais", "vinte e dois mil reais"]:
        assert money_amounts(text) == []


MIL_E_DIGITS_LIMITATION = (
    'Documented limitation (D-36): main\'s parser reads the digits of "mil e 500 reais" '
    "as R$500, so a document written that way authorizes R$500."
)


@pytest.mark.parametrize(
    ("document", "answer"),
    [
        ("O frete é grátis acima de vinte mil reais.", "O frete é grátis acima de R$1000. [S1]"),
        ("O frete é grátis acima de 1,5 mil reais.", "O frete é grátis acima de R$5000. [S1]"),
        (
            "O frete é grátis acima de dois mil e quinhentos reais.",
            "O frete é grátis acima de R$2000. [S1]",
        ),
        (
            "O frete é grátis acima de vinte e dois mil reais.",
            "O frete é grátis acima de R$2000. [S1]",
        ),
        ("O frete é grátis acima de duzentas mil reais.", "O frete é grátis acima de R$1000. [S1]"),
        pytest.param(
            "O frete é grátis acima de mil e 500 reais.",
            "O frete é grátis acima de R$500. [S1]",
            marks=pytest.mark.xfail(strict=True, reason=MIL_E_DIGITS_LIMITATION),
        ),
    ],
)
def test_number_word_documents_do_not_authorize_digit_amounts(document, answer):
    """V3b/V3c: a document written in number words never backs a different R$ claim."""
    assert (
        output_violation(
            answer,
            records=[],
            labels={"S1"},
            instructions="",
            policy_text="",
            documents=[document],
        )
        == "price"
    )


def test_word_budget_is_not_echoed_as_digits():
    """Main's behavior fails closed: a word budget does not exempt "até R$1000"."""
    records = _catalog(("Yamaha C40 Nylon Natural", "599.90"))
    assert (
        output_violation(
            "Sim, temos violões até R$1000. Yamaha C40 Nylon Natural por R$599.90.",
            records=records,
            labels=set(),
            instructions="",
            policy_text="",
            current_message="tem violão até mil reais?",
        )
        == "price"
    )


V3D_DIGIT_BUDGET_PRICE = "Até R$1000 é o preço do Violão Aurora."
V3D_WORD_BUDGET_PRICES = [
    "Até mil reais é o preço do Violão Aurora.",
    "Por até mil reais, você leva o Violão Aurora.",
    "Até mil reais: é o preço do Violão Aurora.",
    "O Violão Aurora custa mil reais.",
]
V3D_ARTICLE_AFTER_PRICE = [
    "Por R$1234,50 um Violão Aurora pode ser seu.",
    "O Violão Aurora sai por R$1234,50 uma unidade.",
]


@pytest.mark.parametrize(
    "answer",
    [
        V3D_DIGIT_BUDGET_PRICE,
        "Até R$1000: o Violão Aurora custa mil reais.",
        *(
            pytest.param(answer, marks=pytest.mark.xfail(strict=True, reason=WORD_PRICE_LIMITATION))
            for answer in V3D_WORD_BUDGET_PRICES
        ),
    ],
)
def test_false_product_price_next_to_word_budget_is_rejected(answer):
    assert (
        output_violation(
            answer,
            records=_catalog(("Violão Aurora", "1234.50")),
            labels=set(),
            instructions="",
            policy_text="",
            current_message="Quero um violão até mil reais.",
        )
        == "price"
    )


@pytest.mark.parametrize("answer", V3D_ARTICLE_AFTER_PRICE)
def test_article_after_digit_price_is_not_a_number_expression(answer):
    assert money_amounts(answer) == [Decimal("1234.50")]
    assert (
        output_violation(
            answer,
            records=_catalog(("Violão Aurora", "1234.50")),
            labels=set(),
            instructions="",
            policy_text="",
            current_message="Quanto custa o Violão Aurora?",
        )
        is None
    )


@pytest.mark.parametrize(
    ("answer", "current_message"),
    [
        ("Violão Aurora custa R$1,5 mil reais.", ""),
        *(
            pytest.param(
                answer, message, marks=pytest.mark.xfail(strict=True, reason=WORD_PRICE_LIMITATION)
            )
            for answer, message in [
                ("Violão Aurora custa dois mil e quinhentos reais.", ""),
                ("Temos opções até vinte e dois mil reais.", "até vinte e dois mil reais"),
                ("Temos opções até três mil reais.", "Quero violão até dois mil reais."),
            ]
        ),
    ],
)
def test_monetary_amounts_in_answer_are_validated(answer, current_message):
    assert (
        output_violation(
            answer,
            records=_catalog(("Violão Aurora", "1234.50")),
            labels=set(),
            instructions="",
            policy_text="",
            current_message=current_message,
        )
        == "price"
    )


@pytest.mark.parametrize(
    "answer",
    [
        "A garantia vale por dois anos. [S1]",
        "Temos apenas três modelos nessa faixa.",
        "O Violão Aurora custa R$ 1.234,50 e temos duas unidades.",
    ],
)
def test_non_monetary_number_words_pass(answer):
    assert (
        output_violation(
            answer,
            records=_catalog(("Violão Aurora", "1234.50")),
            labels={"S1"},
            instructions="",
            policy_text="",
        )
        is None
    )


# --- ChatService reproductions from V3b (integration) ---


async def _ingest(session_factory, tenant, title: str, body: str) -> None:
    async with session_factory() as session:
        doc = await create_document_version(
            session,
            tenant_id=tenant,
            title=title,
            source_type="text",
            filename="doc.txt",
            data=body.encode(),
        )
        await process_document_version(
            session,
            tenant_id=tenant,
            version_id=doc.version_id,
            embedder=HashEmbeddingProvider(),
        )


@pytest.mark.integration
async def test_v3b_false_policy_amount_is_rejected(session_factory, session, commerce):
    await _ingest(
        session_factory,
        commerce,
        "Política de frete",
        (
            "Política de frete: o frete é grátis acima de vinte mil reais. "
            "Consulte as regras de frete."
        ),
    )
    model = ScriptedModel(
        [
            [function_call("search_knowledge", {"query": "política de frete grátis"}, call_id="r")],
            [assistant_message("O frete é grátis acima de R$1000. [S1]")],
        ]
    )
    service, _ = make_service(session_factory, model)
    reply = await turn(service, session, commerce, "Qual é a política de frete?")
    assert reply.handoff is not None
    assert reply.handoff.reason == "low_confidence"
    assert "R$1000" not in reply.reply


@pytest.mark.integration
@pytest.mark.parametrize(
    ("document", "answer"),
    [
        ("O frete é grátis acima de vinte e dois mil reais.", "Grátis acima de R$2000. [S1]"),
        ("O frete é grátis acima de duzentas mil reais.", "Grátis acima de R$1000. [S1]"),
        pytest.param(
            "O frete é grátis acima de mil e 500 reais.",
            "Grátis acima de R$500. [S1]",
            marks=pytest.mark.xfail(strict=True, reason=MIL_E_DIGITS_LIMITATION),
        ),
    ],
)
async def test_v3c_false_policy_amounts_are_rejected(
    session_factory, session, commerce, document, answer
):
    await _ingest(session_factory, commerce, "Frete e devolução", document)
    model = ScriptedModel(
        [
            [function_call("search_knowledge", {"query": "frete grátis acima de"}, call_id="r")],
            [assistant_message(answer)],
        ]
    )
    service, _ = make_service(session_factory, model)
    reply = await turn(service, session, commerce, "Quando o frete é grátis?")
    assert reply.handoff is not None and reply.handoff.reason == "low_confidence"
    assert "R$" not in reply.reply


@pytest.mark.integration
@pytest.mark.parametrize(
    "answer",
    [
        "Violão Aurora custa R$1,5 mil reais.",
        pytest.param(
            "Violão Aurora custa dois mil e quinhentos reais.",
            marks=pytest.mark.xfail(strict=True, reason=WORD_PRICE_LIMITATION),
        ),
    ],
)
async def test_v3c_false_word_product_price_is_rejected(session_factory, session, commerce, answer):
    model = ScriptedModel(
        [
            [function_call("search_products", {"query": "violão"}, call_id="r")],
            [assistant_message(answer)],
        ]
    )
    service, _ = make_service(session_factory, model)
    reply = await turn(service, session, commerce, "Quanto custa o Violão Aurora?")
    assert reply.handoff is not None and reply.handoff.reason == "low_confidence"
    assert "mil" not in reply.reply


@pytest.mark.integration
@pytest.mark.parametrize(
    "answer",
    [
        "Você pode consultar o frete no checkout. [S1]",
        "Para iniciar o pedido de devolução, posso te passar para um atendente. [S1]",
        "Posso explicar como você pode calcular o frete no checkout. [S1]",
        "Posso te orientar a iniciar sua devolução pelo atendimento. [S1]",
    ],
)
async def test_safe_guidance_reaches_customer(session_factory, session, commerce, answer):
    await _ingest(
        session_factory,
        commerce,
        "Frete e devolução",
        "O frete é calculado no checkout. Para devolução, entre em contato com um atendente.",
    )
    model = ScriptedModel(
        [
            [function_call("search_knowledge", {"query": "frete checkout devolução"}, call_id="r")],
            [assistant_message(answer)],
        ]
    )
    service, _ = make_service(session_factory, model)
    reply = await turn(service, session, commerce, "Como funciona o frete?")
    assert reply.handoff is None
    assert answer.split(" [S1]")[0] in reply.reply


@pytest.mark.integration
async def test_wrong_knowledge_category_falls_back_to_unfiltered_search(
    session_factory, session, commerce
):
    """R10 #23: the model filtered by "shipping"; the seeded manual is "politicas"."""
    policy = DATA_DIR / "políticas_da_loja.pdf"
    async with session_factory() as seed_session:
        await ingest_policy(
            seed_session, tenant_id=commerce, data=policy.read_bytes(), filename=policy.name
        )
        await seed_session.commit()
    query = "entrega Dourados cobertura frete Campo Grande região metropolitana outras cidades"
    model = ScriptedModel(
        [
            [
                function_call(
                    "search_knowledge", {"query": query, "category": "shipping"}, call_id="r"
                )
            ],
            [assistant_message("Para outras cidades, o frete é calculado no checkout. [S1]")],
        ]
    )
    service, _ = make_service(session_factory, model)
    reply = await turn(service, session, commerce, "Vocês entregam em Dourados?")
    assert reply.retrieved_chunks > 0 and reply.sources
    assert reply.handoff is None and reply.grounded


@pytest.mark.integration
@pytest.mark.parametrize(
    "answer",
    [
        V3D_DIGIT_BUDGET_PRICE,
        *(
            pytest.param(answer, marks=pytest.mark.xfail(strict=True, reason=WORD_PRICE_LIMITATION))
            for answer in V3D_WORD_BUDGET_PRICES[:2]
        ),
    ],
)
async def test_v3d_budget_echo_cannot_price_product_through_chat(
    session_factory, session, commerce, answer
):
    model = ScriptedModel(
        [
            [function_call("search_products", {"query": "violão"}, call_id="r")],
            [assistant_message(answer)],
        ]
    )
    service, _ = make_service(session_factory, model)
    reply = await turn(service, session, commerce, "Quero um violão até mil reais.")
    assert reply.handoff is not None and reply.handoff.reason == "low_confidence"
    assert not reply.grounded
    assert answer not in reply.reply


@pytest.mark.integration
@pytest.mark.parametrize("answer", V3D_ARTICLE_AFTER_PRICE)
async def test_v3d_article_after_price_reaches_customer(session_factory, session, commerce, answer):
    model = ScriptedModel(
        [
            [function_call("search_products", {"query": "Violão Aurora"}, call_id="r")],
            [assistant_message(answer)],
        ]
    )
    service, _ = make_service(session_factory, model)
    reply = await turn(service, session, commerce, "Quanto custa o Violão Aurora?")
    assert reply.handoff is None and reply.grounded
    assert answer in reply.reply
