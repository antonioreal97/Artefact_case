"""Answer-side live false positives, reproduced without provider calls."""

import pytest
from agents.testing import ScriptedModel, assistant_message

from app.agents.guardrails import NO_INFORMATION, has_factual_claim, output_violation
from app.agents.instructions import POLICY_TEXT, PROMPT_VERSION, build_instructions
from app.core.config import Settings
from app.models import AgentSettings
from app.services.attempt_limiter import InMemoryAttemptLimiter
from app.services.chat import ChatService
from app.services.model_gateway import GuardedModelProvider
from app.services.vault_store import InMemoryVaultStore

COPA_ANSWER = """Desculpe — não posso ajudar com esse tipo de pergunta aqui. Sou o Assistente do Empório da Música e atuo só sobre a loja (instrumentos musicais, preços, estoque, políticas e atendimento).

Posso, por exemplo:
- Buscar violões, teclados ou baterias disponíveis;
- Ver preços e promoções atuais;
- Explicar formas de pagamento e como é o cálculo de frete para Campo Grande;
- Ajudar a localizar um produto específico.

O que você quer que eu procure na loja?"""  # noqa: E501
CONTACT_ANSWER = """Posso checar — já tenho o pedido [[ORDER_1]], mas preciso confirmar a identidade. Por favor, envie o e‑mail ou o telefone que você usou na compra (ou, se tiver, o contact_token). Não envie CPF, documentos ou outras informações sensíveis.

Assim que você passar esse contato, eu verifico o status do pedido. Quer que eu continue?"""  # noqa: E501
GREETING = (
    "Olá! Bem-vindo ao Empório da Música — sua loja em Campo Grande-MS "
    "especializada em instrumentos musicais. Como posso ajudar hoje?"
)
BUSINESS = (
    "Empório da Música, em Campo Grande-MS. Especializada exclusivamente em instrumentos "
    "musicais; não comercializa acessórios. Sua música começa aqui. Fundada em 2002."
)
PERSONA = ("Empório da Música", "Assistente Empório da Música", BUSINESS)


def violation(answer):
    return output_violation(answer, records=[], labels=set(), instructions="", policy_text="")


@pytest.mark.parametrize(
    "answer",
    [
        COPA_ANSWER,
        "Posso buscar violões disponíveis.",
        "Posso ver os disponíveis.",
        "- Checar estoque de violões;",
        "Consultar os disponíveis.",
        "Filtrar os produtos em estoque.",
        "Quer que eu filtre só os disponíveis?",
        "Posso consultar o Yamaha C40 disponível.",
        "Atuo sobre preços, estoque e políticas da loja.",
    ],
)
def test_capabilities_and_topics_are_not_claims(answer):
    assert violation(answer) is None
    assert not has_factual_claim(answer, persona=PERSONA)


@pytest.mark.parametrize(
    "answer",
    [
        "Temos 3 unidades do Yamaha C40 em estoque",
        "Há violões disponíveis.",
        "Tem violão em estoque.",
        "Está disponível.",
        "Estão em estoque.",
        "Encontrei violões disponíveis.",
        "Yamaha C40 disponível.",
        "Yamaha C40 esgotado.",
        "Posso buscar violões, mas temos 3 unidades em estoque.",
        "- Buscar violões disponíveis; Yamaha C40 esgotado.",
        "Posso consultar; temos violões em estoque.",
        "Quer que eu filtre? Temos violões em estoque.",
    ],
)
def test_asserted_stock_still_requires_tools(answer):
    assert violation(answer) == "stock"


@pytest.mark.parametrize(
    "answer",
    [
        CONTACT_ANSWER,
        "Informe o e-mail ou telefone usado na compra para consultar o status do pedido.",
        "Me passe o contato usado na compra para consultar a entrega.",
        "Não envie CPF ou documentos.",
        "Para checar o status, preciso do telefone usado na compra.",
        "Posso checar o pedido [[ORDER_123]].",
    ],
)
def test_placeholders_and_contact_requests_are_not_facts(answer):
    assert not has_factual_claim(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "Seu pedido [[ORDER_1]] foi enviado.",
        "Seu pedido [[ORDER_1]] chega em 7 dias.",
        "Envie seu contato; entrega em 7 dias.",
        "Preciso confirmar sua identidade, mas o frete é grátis.",
        "Olá! Hoje tudo com 20% de desconto.",
        "Posso verificar preços, mas custa R$ 900.",
        "Fundada em 2003.",
        "O prazo é de 2002 dias.",
        "Desconto de 2002%.",
    ],
)
def test_requests_and_persona_cannot_authorize_unrelated_facts(answer):
    assert has_factual_claim(answer, persona=PERSONA)


def test_configured_persona_phrases_cover_only_configured_values():
    assert not has_factual_claim(GREETING, persona=PERSONA)
    assert not has_factual_claim("Olá! Fundada em 2002.", persona=PERSONA)
    assert has_factual_claim("Olá! Fundada em 2002.")
    assert not has_factual_claim("Olá! Sua música começa aqui.", persona=PERSONA)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("question", "answer", "reason"),
    [
        ("Quem ganhou a copa de 2002?", COPA_ANSWER, None),
        ("Qual o status do pedido 2?", CONTACT_ANSWER, None),
        ("Olá", GREETING, None),
        ("Olá", "Olá! Fundada em 2002.", None),
        ("Olá", "Olá! Hoje tudo com 20% de desconto.", "no_information"),
        ("Olá", "Olá! Entrega em 2002 dias.", "no_information"),
        ("Olá", "Olá! Fundada em 2003.", "no_information"),
        ("Tem Yamaha?", "Temos 3 unidades do Yamaha C40 em estoque", "low_confidence"),
    ],
)
async def test_no_tool_answers_through_chat(session_factory, two_tenants, question, answer, reason):
    tenant, _ = two_tenants
    async with session_factory() as session:
        config = await session.get(AgentSettings, tenant)
        config.assistant_name = "Assistente Empório da Música"
        config.business_description = BUSINESS
        await session.commit()
    model = ScriptedModel([[assistant_message(answer)]])
    service = ChatService(
        session_factory,
        Settings(app_env="test", llm_provider="fake", embedding_provider="hash"),
        InMemoryVaultStore(),
        lambda settings, allowlist: GuardedModelProvider(model, allowlist),
        limiter=InMemoryAttemptLimiter(),
    )
    async with session_factory() as session:
        reply = await service.handle_message(
            session,
            tenant_id=tenant,
            conversation_id=None,
            text=question,
            correlation_id="round-three",
        )
    assert not reply.grounded
    if reason is None:
        assert reply.handoff is None
        assert reply.reply == answer.replace("[[ORDER_1]]", "2")
    else:
        assert reply.handoff and reply.handoff.reason == reason
        if reason == "no_information":
            assert reply.reply.startswith(NO_INFORMATION)


def test_prompt_uses_customer_language_and_includes_configured_tagline():
    assert PROMPT_VERSION == "commerce-privacy-v7"
    assert "Nunca mencione nomes internos como contact_token, order_token, tool" in POLICY_TEXT
    assert 'Solicite apenas "e-mail/telefone da compra"' in POLICY_TEXT
    config = AgentSettings(assistant_name="Assistente", extra={"tagline": "Desde 2002."})
    assert "Slogan: Desde 2002." in build_instructions(config)


def test_natural_contact_request_does_not_match_prompt_copy_guard():
    assert (
        output_violation(
            "Não foi possível confirmar os dados do pedido. "
            "Confira o e-mail ou telefone usado na compra.",
            records=[],
            labels=set(),
            instructions="",
            policy_text=POLICY_TEXT,
        )
        is None
    )
