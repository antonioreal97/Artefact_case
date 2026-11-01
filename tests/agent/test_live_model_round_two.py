"""Second live-round reproductions using seed catalog data and scripted local models."""

import uuid
from decimal import Decimal

import pytest
from agents.testing import ScriptedModel, assistant_message, function_call

from app.agents.guardrails import money_amounts, output_violation
from app.core.config import Settings
from app.integrations.base import ProductQuery
from app.integrations.local_commerce import LocalCommerceBackend
from app.models import AgentSettings, CommerceCategory, CommerceProduct, CommercePromotion
from app.rag.embeddings import HashEmbeddingProvider
from app.rag.ingestion import create_document_version, process_document_version
from app.services.attempt_limiter import InMemoryAttemptLimiter
from app.services.chat import ChatService
from app.services.fake_model import FakeCommerceModel
from app.services.model_gateway import GuardedModelProvider
from app.services.privacy import PrivacyBoundaryError, public_persona_values, scan_outbound
from app.services.vault_store import InMemoryVaultStore
from app.tools.context import ToolCallRecord
from scripts.commerce import load_commerce
from scripts.create_tenant import PUBLIC_CONTACT_VALUES, create_tenant

GUITAR_ANSWER = """No momento não há promoções ativas para guitarras no nosso catálogo.
Entre as guitarras disponíveis, os menores preços retornados são:
- Fender Telecaster Player Series — R$ 5.400,00
- PRS SE Custom 24 — R$ 6.800,00
- Ibanez RG550 Genesis Collection — R$ 7.500,00
- Fender Stratocaster American Pro II — R$ 12.500,00
- Gibson Les Paul Standard '50s — R$ 18.900,00
O modelo com menor preço retornado é a Fender Telecaster Player Series (R$ 5.400,00).
Não posso prometer que seja o menor preço da loja além dos resultados retornados.
Quer que eu procure promoções filtrando por tipo (elétrica/acústica), marca ou faixa de preço específica?"""  # noqa: E501
ADDRESS = "Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333"


@pytest.fixture
async def seed_catalog(session_factory, two_tenants):
    tenant, other = two_tenants
    exports = load_commerce()
    async with session_factory() as session:
        session.add_all(CommerceCategory(tenant_id=tenant, **row) for row in exports["categories"])
        session.add_all(CommerceProduct(tenant_id=tenant, **row) for row in exports["products"])
        session.add(
            CommerceProduct(
                tenant_id=other,
                external_id=1,
                category_external_id=1,
                name="Yamaha C40 segredo",
                price=Decimal("1"),
                stock_quantity=5,
                status="active",
            )
        )
        await session.commit()
    return LocalCommerceBackend(session_factory, tenant)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("query", "count", "name"),
    [
        ("Yamaha C40 estoque", 1, "Yamaha C40"),
        ("Yamaha C40", 1, "Yamaha C40"),
        ("guitarra promoção", 5, ""),
        ("guitarra", 5, ""),
        ("Tem violão Yamaha?", 7, "Yamaha"),
        ("Vocês têm Yamaha C40 disponível? Quanto custa?", 1, "Yamaha C40"),
        ("Yamaha C40 inexistente", 11, "Yamaha C40"),
        ("inexistente xyz", 0, ""),
    ],
)
async def test_seed_intent_and_partial_search(seed_catalog, query, count, name):
    results = await seed_catalog.search_products(ProductQuery(text=query, limit=20))
    assert len(results) == count
    if name:
        assert name in results[0].name
    assert all("segredo" not in item.name for item in results)


@pytest.mark.integration
async def test_partial_search_keeps_filters_and_and_precedence(seed_catalog):
    results = await seed_catalog.search_products(
        ProductQuery(
            text="Yamaha C40 inexistente",
            category="Violões",
            in_stock_only=True,
            min_price=Decimal("400"),
            max_price=Decimal("900"),
            limit=1,
        )
    )
    assert len(results) == 1 and results[0].name == "Yamaha C40 Nylon Natural"
    assert results[0].available and Decimal("400") <= results[0].price <= Decimal("900")
    assert (
        await seed_catalog.search_products(
            ProductQuery(text="Yamaha C40 inexistente", category="Guitarras")
        )
        == []
    )
    # Exact matches exclude the other partial Yamaha matches.
    assert len(await seed_catalog.search_products(ProductQuery(text="Yamaha C40"))) == 1


def guitar_records():
    products = [p for p in load_commerce()["products"] if p["category_external_id"] == 1]
    return [
        ToolCallRecord(
            tool_name="search_products",
            arguments_redacted={"query": "guitarra"},
            status="success",
            error_code=None,
            latency_ms=0,
            result_summary={
                "data": {
                    "items": [
                        {
                            "name": p["name"],
                            "price": str(p["price"]),
                            "available": True,
                            "active_promotion": None,
                        }
                        for p in products
                    ]
                }
            },
        )
    ]


def price_rule(answer):
    return output_violation(
        answer, records=guitar_records(), labels=set(), instructions="", policy_text=""
    )


def test_exact_live_guitar_answer_and_currency_parser():
    assert money_amounts("PRS SE Custom 24 — R$ 6.800,00") == [Decimal("6800")]
    assert money_amounts("Custa 24 reais") == [Decimal("24")]
    assert price_rule(GUITAR_ANSWER) is None
    assert price_rule(GUITAR_ANSWER.replace("\n", " / ")) is None


@pytest.mark.parametrize("amount", ["5.400,00", "6.800,00", "7.500,00", "12.500,00", "18.900,00"])
def test_wrong_guitar_prices_still_fail(amount):
    assert price_rule(GUITAR_ANSWER.replace(amount, "9.999,00")) == "price"


@pytest.mark.parametrize(
    "text",
    [
        "Sou o Assistente do Empório da Música.",
        "Sou o Assistente.",
        "Sou a Assistente virtual.",
        "Sou a Assistente da Empório da Música.",
        "Sou o Assistente virtual do Empório da Música.",
        "Sou o Assistente virtual da Empório da Música.",
        "Me chamo Assistente Empório da Música.",
        "Sou o Empório da Música.",
    ],
)
def test_tenant_persona_introductions(text):
    scan_outbound(
        text, allowlist=public_persona_values("Assistente Empório da Música", "Empório da Música")
    )


@pytest.mark.parametrize(
    "text",
    [
        "Sou o Carlos Ferreira.",
        "Sou o Assistente João Silva.",
        "Sou a Maria Souza.",
        "Me chamo Carlos Ferreira.",
        "Sou o Assistente da Loja Secreta.",
        "Sou o Assistente do Empório da Música. Me chamo Carlos Ferreira.",
    ],
)
def test_persona_allowlist_does_not_hide_customers(text):
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound(
            text,
            allowlist=public_persona_values("Assistente Empório da Música", "Empório da Música"),
        )


@pytest.mark.parametrize(
    "text", [ADDRESS, "CEP 79202-333", "CEP 79202-333.", "Rua 14 de Maio, 3200"]
)
def test_public_postal_values(text):
    scan_outbound(text, allowlist=PUBLIC_CONTACT_VALUES)
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound(text, allowlist=[])


@pytest.mark.parametrize("text", ["CEP 79002-444", "Rua dos Clientes, 100", "CEP 79002444"])
def test_private_postal_values_remain_blocked(text):
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound(text, allowlist=PUBLIC_CONTACT_VALUES)


@pytest.mark.integration
async def test_existing_seed_gets_public_address_without_overwriting_persona(
    session_factory, monkeypatch
):
    monkeypatch.setattr("scripts.create_tenant.TENANT_SLUG", f"r5-seed-{uuid.uuid4().hex[:8]}")
    async with session_factory() as session:
        tenant, _ = await create_tenant(session)
        config = await session.get(AgentSettings, tenant)
        config.public_contact_values = ["publico@exemplo.test"]
        config.assistant_name = "Persona personalizada"
        await session.commit()
        for _ in range(2):
            await create_tenant(session)
            await session.commit()
        assert config.assistant_name == "Persona personalizada"
        assert config.public_contact_values == ["publico@exemplo.test", *PUBLIC_CONTACT_VALUES]
        scan_outbound(
            f"Nosso endereço: {ADDRESS}; CEP 79202-333", allowlist=config.public_contact_values
        )


async def configure_docs(session_factory, tenant, contents):
    async with session_factory() as session:
        config = await session.get(AgentSettings, tenant)
        config.public_contact_values = list(PUBLIC_CONTACT_VALUES)
        config.retrieval_min_similarity = 0.0
        await session.commit()
        for index, content in enumerate(contents):
            receipt = await create_document_version(
                session,
                tenant_id=tenant,
                title=f"Política seção {index}",
                source_type="text",
                filename=f"politica{index}.txt",
                data=content.encode(),
            )
            await process_document_version(
                session,
                tenant_id=tenant,
                version_id=receipt.version_id,
                embedder=HashEmbeddingProvider(),
            )


async def scripted_chat(session_factory, tenant, steps, question):
    model = ScriptedModel(steps)
    service = ChatService(
        session_factory,
        Settings(app_env="test", llm_provider="fake", embedding_provider="hash"),
        InMemoryVaultStore(),
        lambda settings, allowlist: GuardedModelProvider(model, allowlist),
        limiter=InMemoryAttemptLimiter(),
    )
    async with session_factory() as session:
        return await service.handle_message(
            session,
            tenant_id=tenant,
            conversation_id=None,
            text=question,
            correlation_id="round-two",
        )


@pytest.mark.integration
@pytest.mark.parametrize("private", [False, True])
async def test_public_address_answer_survives_chat_scan(session_factory, two_tenants, private):
    tenant, _ = two_tenants
    await configure_docs(session_factory, tenant, [f"Endereço da loja: {ADDRESS}. CEP 79202-333."])
    answer = f"Nosso endereço é {ADDRESS}. CEP 79202-333. [S1]"
    if private:
        answer += " Endereço do cliente: Rua dos Clientes, 100; CEP 79002-444."
    result = await scripted_chat(
        session_factory,
        tenant,
        [
            [function_call("search_knowledge", {"query": "endereço da loja"}, call_id="address")],
            [assistant_message(answer)],
        ],
        "Qual o endereço da loja?",
    )
    if private:
        assert result.handoff and result.handoff.reason == "privacy_guard"
        assert not result.grounded and "79002-444" not in result.reply
    else:
        assert result.handoff is None and result.grounded
        assert ADDRESS in result.reply


@pytest.mark.integration
async def test_persona_survives_chat_answer_scan(session_factory, two_tenants):
    tenant, _ = two_tenants
    result = await scripted_chat(
        session_factory,
        tenant,
        [
            [
                assistant_message("Sou o Assistente do Empório da Música."),
                function_call("search_products", {"query": "guitarra"}, call_id="persona"),
            ],
            [assistant_message("Sou o Assistente do Empório da Música. Posso ajudar com a loja?")],
        ],
        "Quem ganhou a copa de 2002?",
    )
    assert result.handoff is None
    assert "Sou o Assistente do Empório da Música" in result.reply


@pytest.mark.integration
async def test_conflicting_sources_keeps_agreed_answer_and_both_citations(
    session_factory, two_tenants
):
    tenant, _ = two_tenants
    await configure_docs(
        session_factory,
        tenant,
        [
            "Parcelamento no cartão de crédito: até 12x sem juros, parcela mínima R$ 100.",
            "Parcelamento no cartão de crédito: até 12x sem juros. Até 3x sem mínimo, "
            "4–6x mínimo R$ 80, 7–12x mínimo R$ 100.",
        ],
    )
    answer = (
        "Parcelamos até 12x sem juros no cartão de crédito. [S1] [S2] "
        "As fontes divergem sobre a parcela mínima; solicitei confirmação humana desse detalhe."
    )
    result = await scripted_chat(
        session_factory,
        tenant,
        [
            [
                function_call(
                    "search_knowledge",
                    {"query": "parcelamento cartão de crédito"},
                    call_id="payment",
                )
            ],
            [
                function_call(
                    "request_human_handoff",
                    {"reason": "conflicting_sources", "summary": "Confirmar parcela mínima"},
                    call_id="handoff",
                )
            ],
            [assistant_message(answer)],
        ],
        "Vocês parcelam no cartão?",
    )
    assert result.handoff and result.handoff.reason == "conflicting_sources"
    assert result.grounded and answer in result.reply
    assert {s.label for s in result.sources} == {"S1", "S2"}


def test_public_email_sentence_boundary_does_not_allow_domain_suffix():
    scan_outbound("Contato: contato@emporiodamusica.com.br.", allowlist=PUBLIC_CONTACT_VALUES)
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound("contato@emporiodamusica.com.br.evil.test", allowlist=PUBLIC_CONTACT_VALUES)


def test_other_real_guitar_price_cannot_justify_wrong_named_price():
    assert price_rule(GUITAR_ANSWER.replace("5.400,00", "6.800,00")) == "price"


@pytest.mark.integration
async def test_fallback_ranking_and_all_filters(session_factory, two_tenants):
    tenant, _ = two_tenants
    async with session_factory() as session:
        session.add(CommerceCategory(tenant_id=tenant, external_id=1, name="Violões"))
        for external_id, name, description, stock in [
            (1, "Produto genérico", "Yamaha C40", 3),
            (2, "Yamaha C40", "", 3),
            (3, "Yamaha alternativa", "", 3),
            (4, "Yamaha C40 indisponível", "", 0),
        ]:
            session.add(
                CommerceProduct(
                    tenant_id=tenant,
                    external_id=external_id,
                    category_external_id=1,
                    name=name,
                    description=description,
                    price=Decimal("800"),
                    stock_quantity=stock,
                    status="active",
                )
            )
        session.add(
            CommercePromotion(
                tenant_id=tenant,
                external_id=1,
                product_external_id=2,
                discount_percent=Decimal("25"),
                description="Oferta",
                is_active=True,
            )
        )
        await session.commit()
    backend = LocalCommerceBackend(session_factory, tenant)
    results = await backend.search_products(ProductQuery(text="Yamaha C40 inexistente"))
    assert [p.product_id for p in results] == [2, 4, 1, 3]
    filtered = await backend.search_products(
        ProductQuery(
            text="Yamaha C40 inexistente",
            category="violões",
            in_stock_only=True,
            min_price=Decimal("600"),
            max_price=Decimal("600"),
        )
    )
    assert [p.product_id for p in filtered] == [2]
    assert filtered[0].active_promotion.promotional_price == Decimal("600")
    assert not await backend.search_products(
        ProductQuery(
            text="Yamaha C40 inexistente",
            min_price=Decimal("900"),
        )
    )
    assert not await backend.search_products(
        ProductQuery(
            text="Yamaha C40 inexistente",
            max_price=Decimal("500"),
        )
    )


@pytest.mark.parametrize(
    "question",
    [
        "Vocês fazem serviço de luthieria e regulagem de guitarra na loja?",
        "Qual o valor do conserto do piano?",
        "Fazem manutenção de violão?",
    ],
)
def test_fake_routes_service_inquiries_to_knowledge(question):
    output = FakeCommerceModel()._step(question)
    assert len(output) == 1 and output[0].name == "search_knowledge"
