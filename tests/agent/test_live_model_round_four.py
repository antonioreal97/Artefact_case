"""Fourth live-round reproductions; all models and provider requests are offline."""

import asyncio

import pytest
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from pydantic import ValidationError

from app.agents.guardrails import has_factual_claim, output_violation
from app.agents.instructions import POLICY_TEXT
from app.core.config import Settings
from app.integrations.base import ProductQuery
from app.integrations.local_commerce import _search_stems, _term_matches, normalize_text
from tests.agent import test_chat, test_live_model_round_two
from tests.agent.test_chat import make_service, turn
from tests.agent.test_live_model_round_three import (
    PERSONA,
)
from tests.agent.test_live_model_round_three import (
    test_no_tool_answers_through_chat as run_no_tool_answer,
)

commerce = test_chat.commerce
seed_catalog = test_live_model_round_two.seed_catalog

COPA_ANSWER = """Boa pergunta! Mas isso foge ao escopo da Empório da Música — aqui eu ajudo só com assuntos da loja (instrumentos, modelos, preços, estoque, entrega, etc.).

Se quiser, indico onde procurar (ex.: Wikipédia ou sites de esportes) — ou posso ajudar com algo da loja: procurar um violão, teclado, guitarra, comparar preços disponíveis aqui na loja... O que prefere?

Sua música começa aqui."""  # noqa: E501


@pytest.mark.integration
@pytest.mark.parametrize(
    ("text", "category"),
    [
        (None, "violão"),
        (None, "Violão"),
        ("violão", "violão"),
        ("Yamaha", "violão"),
        (None, "violões"),
    ],
)
async def test_exact_live_category_queries(seed_catalog, text, category):
    results = await seed_catalog.search_products(
        ProductQuery(text=text, category=category, max_price=1000, in_stock_only=True)
    )
    reference = await seed_catalog.search_products(
        ProductQuery(text=text, category="violões", max_price=1000, in_stock_only=True)
    )
    assert results == reference
    assert len(results) == (3 if text == "Yamaha" else 10)
    assert all(item.available and item.price <= 1000 for item in results)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("singular", "plural"),
    [
        ("violao", "violoes"),
        ("guitarra", "guitarras"),
        ("bateria", "baterias"),
        ("teclado", "teclados"),
        ("ukulele", "ukuleles"),
    ],
)
async def test_seed_search_and_category_inflections(seed_catalog, singular, plural):
    for field in ("text", "category"):
        left = await seed_catalog.search_products(ProductQuery(**{field: singular}, limit=20))
        right = await seed_catalog.search_products(ProductQuery(**{field: plural}, limit=20))
        assert left and left == right


@pytest.mark.parametrize(
    ("singular", "plural"),
    [
        ("violão", "violões"),
        ("pão", "pães"),
        ("pedal", "pedais"),
        ("papel", "papéis"),
        ("som", "sons"),
        ("guitarra", "guitarras"),
    ],
)
def test_inflections_both_directions(singular, plural):
    for query, category in ((singular, plural), (plural, singular)):
        query, category = normalize_text(query), normalize_text(category)
        assert _term_matches(query, category, _search_stems(category))


@pytest.mark.integration
async def test_unknown_category_is_empty(seed_catalog):
    assert await seed_catalog.search_products(ProductQuery(category="inexistente")) == []


@pytest.mark.parametrize(
    "answer",
    [
        COPA_ANSWER,
        "Assuntos da loja: estoque, preços, entrega.",
        "Comparar preços disponíveis aqui na loja.",
    ],
)
def test_scope_modifiers_are_not_stock_assertions(answer):
    assert (
        output_violation(answer, records=[], labels=set(), instructions="", policy_text=POLICY_TEXT)
        is None
    )
    if answer != "Assuntos da loja: estoque, preços, entrega.":
        assert not has_factual_claim(answer, persona=PERSONA)


@pytest.mark.parametrize(
    "answer",
    [
        "Temos 3 unidades do Yamaha C40 em estoque",
        "O Yamaha C40 está disponível",
        "Existem violões disponíveis.",
        "O Yamaha C40 esgotou.",
        "Yamaha C40 disponível para pronta entrega.",
        "Posso ajudar com a loja, mas o Yamaha C40 está disponível.",
    ],
)
def test_unsupported_assertions_still_fail(answer):
    assert (
        output_violation(answer, records=[], labels=set(), instructions="", policy_text="")
        == "stock"
    )


@pytest.mark.integration
async def test_verbatim_refusal_survives_chat(session_factory, two_tenants):
    await run_no_tool_answer(
        session_factory, two_tenants, "Quem ganhou a copa de 2002?", COPA_ANSWER, None
    )


@pytest.mark.parametrize("environment", ["test", "prod"])
@pytest.mark.parametrize(
    "field,maximum", [("model_timeout_seconds", 300), ("turn_timeout_seconds", 600)]
)
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), "above_maximum"])
def test_timeout_settings_bounded(environment, field, maximum, value):
    if value == "above_maximum":
        value = maximum + 1
    with pytest.raises(ValidationError, match=field):
        Settings(
            _env_file=None,
            app_env=environment,
            llm_provider="openai",
            embedding_provider="openai",
            api_key_pepper="test-nondefault",
            user_ref_key="test-nondefault",
            **{field: value},
        )


def test_timeout_defaults():
    settings = Settings(_env_file=None, app_env="test")
    assert settings.model_timeout_seconds == 30
    assert settings.turn_timeout_seconds == 75
    assert settings.max_agent_turns == 8
    assert settings.openai_reasoning_effort == "low"


@pytest.mark.parametrize(
    "value,expected", [("", None), ("  ", None), ("minimal", "minimal"), ("high", "high")]
)
def test_reasoning_effort_setting(value, expected):
    settings = Settings(_env_file=None, app_env="test", openai_reasoning_effort=value)
    assert settings.openai_reasoning_effort == expected


@pytest.mark.parametrize("value", ["LOW", "xhigh", "fast"])
def test_reasoning_effort_rejects_unknown(value):
    with pytest.raises(ValidationError, match="openai_reasoning_effort"):
        Settings(_env_file=None, app_env="test", openai_reasoning_effort=value)


@pytest.mark.integration
async def test_multiple_model_calls_can_exceed_single_call_budget(
    session_factory, session, commerce
):
    async def lookup(call):
        await asyncio.sleep(0.12)
        return [function_call("search_products", {"text": "Aurora"}, call_id="search")]

    async def answer(call):
        await asyncio.sleep(0.12)
        return [assistant_message("Violão Aurora: R$ 1.234,50.")]

    model = ScriptedModel([ModelStep.respond(lookup), ModelStep.respond(answer)])
    service, _ = make_service(
        session_factory,
        model,
        model_timeout_seconds=0.2,
        turn_timeout_seconds=2,
    )
    reply = await turn(service, session, commerce, "Qual o preço do Violão Aurora?")
    assert reply.handoff is None and reply.grounded
    assert reply.tools_used == ["search_products"]
    assert len(model.calls) == 2


@pytest.mark.integration
async def test_turn_budget_still_limits_agent_loop(session_factory, session, commerce):
    async def lookup(call):
        await asyncio.sleep(0.08)
        return [function_call("search_products", {"text": "Aurora"}, call_id="search")]

    async def answer(call):
        await asyncio.sleep(0.3)
        return [assistant_message("Nunca alcançado")]

    model = ScriptedModel([ModelStep.respond(lookup), ModelStep.respond(answer)])
    service, _ = make_service(
        session_factory,
        model,
        model_timeout_seconds=1,
        turn_timeout_seconds=0.2,
    )
    reply = await turn(service, session, commerce, "Qual o preço do Violão Aurora?")
    assert reply.handoff.reason == "tool_failure" and not reply.grounded
    assert len(model.calls) == 2 and "Nunca alcançado" not in reply.reply


def test_prompt_targets_live_tool_and_handoff_misuse():
    assert "não repita get_price" in POLICY_TEXT
    assert "Use check_stock somente" in POLICY_TEXT
    assert "NUNCA chame request_human_handoff" in POLICY_TEXT
    assert "contradizem o fato específico" in POLICY_TEXT
