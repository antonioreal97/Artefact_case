"""Generate five offline conversations: uv run python -m scripts.demo_conversations."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.database import Base
from app.core.logging import configure_logging
from app.models import AgentSettings, Chunk, CommerceCustomer, CommerceOrder
from app.schemas.chat import ChatReply
from app.services.attempt_limiter import InMemoryAttemptLimiter
from app.services.chat import ChatService
from app.services.privacy_vault import TOKEN_RE
from app.services.vault_store import InMemoryVaultStore
from scripts.create_tenant import TRIAGE_CONFIG
from scripts.eval_rag import (
    ADMIN_URL_DEFAULT,
    ROOT,
    _create_database,
    _drop_database,
    mode_environment,
    seed_tenants,
)

DEFAULT_OUTPUT_DIR = ROOT / "examples" / "conversations"

# Exact fixture translations: unknown dialogue fails export instead of silently
# publishing a missing/stale translation. Numeric catalog values stay verbatim.
_TRANSLATIONS = {
    "Como posso te ajudar?": "How can I help you?",
    "1. Horário e endereço da loja": "1. Store hours and address",
    "2. Status do meu pedido": "2. My order status",
    "3. Trocas e devoluções": "3. Exchanges and returns",
    "4. Produtos, preços e estoque": "4. Products, prices and stock",
    "5. Falar com um atendente": "5. Talk to a staff member",
    "Responda com o número ou escreva sua dúvida.": "Reply with a number or write your question.",
    (
        "Horário: Segunda a sexta, das 9h às 18h; sábado, das 9h às 13h. "
        "Domingo e feriados, fechado."
    ): (
        "Hours: Monday to Friday, 9 am to 6 pm; Saturday, 9 am to 1 pm. "
        "Closed Sundays and holidays."
    ),
    "Endereço: Rua 14 de Maio, 3200, Centro, Campo Grande - MS, 79202-333": (
        "Address: Rua 14 de Maio, 3200, Centro, Campo Grande - MS, 79202-333"
    ),
    "Telefone / WhatsApp: (67) 3341-4444": "Phone / WhatsApp: (67) 3341-4444",
    "E-mail: contato@emporiodamusica.com.br": "Email: contato@emporiodamusica.com.br",
    "Quais opções de violões disponíveis custando até R$1000?": (
        "Which acoustic guitars are available for up to R$1,000?"
    ),
    "Qual o preço do Yamaha C40?": ("What is the price of the Yamaha C40?"),
    "Quanto custa o Takamine GD20?": ("How much does the Takamine GD20 cost?"),
    "Takamine GD20 disponível em estoque?": ("Is the Takamine GD20 available in stock?"),
    "Me arrependi da minha compra, posso devolver meu pedido?": (
        "I changed my mind about my purchase; can I return my order?"
    ),
    "Quais as condições para devolução por arrependimento e a nota fiscal?": (
        "What are the conditions for a change-of-mind return, including the invoice?"
    ),
    "Qual o status do meu pedido? [número informado; e-mail incorreto informado]": (
        "What is my order status? [number provided; incorrect email provided]"
    ),
    "Vou corrigir o contato. [mesmo número informado; e-mail correto informado]": (
        "I will correct the contact. [same number provided; correct email provided]"
    ),
    "Qual o endereço da loja?": ("What is the store address?"),
    "Qual é a capital da Islândia?": ("What is the capital of Iceland?"),
    (
        "Oi! Aqui é o Empório da Música. Atendimento de segunda a sexta, das 9h às 18h, "
        "e sábado das 9h às 13h (domingo e feriado, fechado). "
        "Posso te ajudar com instrumentos, preços, estoque, pedidos e nossas políticas."
    ): (
        "Hi! This is Empório da Música. We're here Monday to Friday, 9 am to 6 pm, "
        "and Saturday 9 am to 1 pm (closed Sundays and holidays). "
        "I can help with instruments, prices, stock, orders and our policies."
    ),
    "4.1 Direito de Arrependimento (Compras Online)": ("4.1 Right to Withdraw (Online Purchases)"),
    (
        "• O cliente pode solicitar a devolução em até 7 (sete) dias corridos após o recebimento do"
    ): ("• The customer can request a return within 7 (seven) calendar days of receiving the"),
    "produto, sem necessidade de justificativa.": ("product, without giving a reason."),
    ("• O produto deve estar em sua embalagem original, sem sinais de uso, com todos os"): (
        "• The product must be in its original packaging, with no signs of use, and include all"
    ),
    "acessórios e manuais.": ("accessories and manuals."),
    ("• O reembolso será realizado na mesma forma de pagamento original em até 10 dias úteis."): (
        "• The refund will use the original payment method within 10 business days."
    ),
    "• O frete de devolução é por conta da loja em caso de arrependimento.": (
        "• The store pays return shipping for change-of-mind returns."
    ),
    "Políticas da Loja": ("Store Policies"),
    (
        "Não consegui confirmar os dados do pedido. Confere o número e o e-mail ou "
        "telefone usado na compra?"
    ): (
        "I couldn't confirm the order details. Can you check the order number and the "
        "email or phone used for the purchase?"
    ),
    "Situação: entregue.": ("Status: delivered."),
    "1.2 Dados da Empresa": ("1.2 Company Details"),
    "Campo Informação": ("Field Information"),
    "Razão Social Empório da Música Instrumentos Musicais Ltda.": (
        "Legal Name Empório da Música Instrumentos Musicais Ltda."
    ),
    "Nome Fantasia Empório da Música": ("Trading Name Empório da Música"),
    "CNPJ 12.345.678/0001-90": ("Business Tax ID (CNPJ) 12.345.678/0001-90"),
    "Endereço Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333": (
        "Address Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333"
    ),
    "Telefone / WhatsApp (67) 3341-4444": ("Phone / WhatsApp (67) 3341-4444"),
    "E-mail contato@empóriodamusica.com.br": ("Email contato@empóriodamusica.com.br"),
    "Fundação 2008": ("Founded 2008"),
    (
        "Posso te ajudar só com assuntos da loja: produtos, preços, estoque, pedidos e "
        "nossas políticas."
    ): ("I can only help with store topics: products, prices, stock, orders, and our policies."),
}


def _translate(text: str) -> str:
    if text in _TRANSLATIONS:
        return _TRANSLATIONS[text]
    if re.fullmatch(r"[\w -]+: R\$ [\d.,]+\.", text):
        return text  # Product proper names and BRL prices need no translation.
    if text.endswith(": disponível em estoque."):
        return text.removesuffix(": disponível em estoque.") + ": available in stock."
    for prefix, english in (
        ("Rastreamento: ", "Tracking: "),
        ("Previsão de entrega: ", "Estimated delivery: "),
    ):
        if text.startswith(prefix):
            return english + text.removeprefix(prefix)
    citation = re.fullmatch(r"(.*) (\[S\d+\])", text)
    if citation:
        return _translate(citation[1]) + " " + citation[2]
    source = re.fullmatch(r"Fonte(?: \[(S\d+)\])?: (.*), §(.*) \(p\. (\d+)\)", text)
    if source:
        label = f" [{source[1]}]" if source[1] else ""
        return f"Source{label}: {_translate(source[2])}, §{_translate(source[3])} (p. {source[4]})"
    raise ValueError(f"Missing demo English translation: {text!r}")


def _english(text: str) -> str:
    lines = [_translate(line) if line else "" for line in text.splitlines()]
    return "> EN: " + lines[0] + "".join("\n>" + (" " + line if line else "") for line in lines[1:])


@dataclass(frozen=True)
class DemoTurn:
    text: str
    # Restricted inputs are used locally but never exported in tool traces or client text.
    display: str | None = None


@dataclass(frozen=True)
class Demo:
    filename: str
    title: str
    turns: tuple[DemoTurn, ...]
    note: str = ""


async def _demos(factory: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID) -> list[Demo]:
    async with factory() as session:
        row = (
            await session.execute(
                select(CommerceOrder.external_id, CommerceCustomer.email)
                .join(
                    CommerceCustomer,
                    (
                        (CommerceCustomer.tenant_id == tenant_id)
                        & (CommerceCustomer.external_id == CommerceOrder.customer_external_id)
                    ),
                )
                .where(
                    CommerceOrder.tenant_id == tenant_id, CommerceOrder.tracking_code.is_not(None)
                )
                .order_by(CommerceOrder.external_id)
                .limit(1)
            )
        ).one()
    order, email = row
    return [
        Demo(
            "01-catalog-guitars-under-1000.md",
            "Available acoustic guitars under R$ 1,000",
            (
                DemoTurn("Quais opções de violões disponíveis custando até R$1000?"),
                DemoTurn("Qual o preço do Yamaha C40?"),
            ),
        ),
        Demo(
            "02-price-takamine-gd20.md",
            "Takamine GD20 price and stock",
            (
                DemoTurn("Quanto custa o Takamine GD20?"),
                DemoTurn("Takamine GD20 disponível em estoque?"),
            ),
        ),
        Demo(
            "03-return-policy.md",
            "Change-of-mind returns and conditions",
            (
                DemoTurn("Me arrependi da minha compra, posso devolver meu pedido?"),
                DemoTurn("Quais as condições para devolução por arrependimento e a nota fiscal?"),
            ),
            "The deterministic model rewrites the query: arrependi → arrependimento, "
            "devolver → devolução. Without rewriting, the original question returns "
            "no_information with hash embeddings; this retrieval limitation remains. "
            "The reply cites the retrieved section without assessing individual eligibility.",
        ),
        Demo(
            "04-verified-order-status.md",
            "Order: incorrect contact and successful verification",
            (
                DemoTurn(
                    f"Qual o status do pedido {order}? Email: incorreto@example.test",
                    "Qual o status do meu pedido? [número informado; e-mail incorreto informado]",
                ),
                DemoTurn(
                    f"Qual o status do pedido {order}? Email: {email}",
                    "Vou corrigir o contato. [mesmo número informado; e-mail correto informado]",
                ),
            ),
            "Order status uses deterministic triage and the existing verified lookup tool. "
            "Seed data is fictitious. Personal inputs are omitted from this example; "
            "only the final customer reply shows authorized details, revealed locally "
            "after verification. No model payload is exported.",
        ),
        Demo(
            "05-out-of-scope-and-address.md",
            "Store address and an out-of-scope question",
            (
                DemoTurn("Qual o endereço da loja?"),
                DemoTurn("Qual é a capital da Islândia?"),
            ),
            "The address shortcut uses deterministic triage and public tenant configuration "
            "without a model or RAG call (grounded=false). An unrelated question then reaches "
            "the agent and is refused without a human handoff or factual grounding.",
        ),
    ]


def _render_turn(index: int, turn: DemoTurn, reply: ChatReply) -> str:
    sources = (
        "; ".join(
            f"[{source.label}] {_translate(source.title)}, "
            f"{_translate(source.section) if source.section else 'no section'}, page {source.page}"
            for source in reply.sources
        )
        or "none"
    )
    customer = turn.display or turn.text
    return "\n".join(
        [
            f"## Turn {index}",
            "",
            f"**Customer:** {customer}",
            "",
            _english(customer),
            "",
            *(
                [
                    f"**Agent (welcome):**\n\n{reply.welcome_message}",
                    "",
                    _english(reply.welcome_message),
                    "",
                ]
                if reply.welcome_message
                else []
            ),
            f"**Agent:**\n\n{reply.reply}",
            "",
            _english(reply.reply),
            "",
            f"- Route: {reply.route or 'agent'}.",
            f"- Tools: {', '.join(reply.tools_used) or 'none'}.",
            f"- Citations: {sources}.",
            f"- Handoff: {reply.handoff.reason if reply.handoff else 'none'}.",
            f"- Grounded: {str(reply.grounded).lower()}.",
            "",
        ]
    )


async def generate_conversations(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[Path]:
    """Use an isolated DB, real seed/tools/chat, and always drop the temporary database."""
    admin = os.environ.get("TEST_DATABASE_ADMIN_URL", ADMIN_URL_DEFAULT)
    name = f"demo_{uuid.uuid4().hex[:10]}"
    with mode_environment(live=False):
        url = await _create_database(admin, name)
        try:
            engine = create_async_engine(url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                    await conn.run_sync(Base.metadata.create_all)
                factory = async_sessionmaker(engine, expire_on_commit=False)
                tenants = await seed_tenants(factory)
                tenant_id = tenants["emporio"].tenant_id
                # Retrieval breaks rank ties by chunk UUID. Stabilize only those IDs
                # in this throw-away DB so identical source data yields identical labels.
                # No content, thresholds, embeddings or retrieval code are changed.
                async with factory() as session:
                    config = await session.get(AgentSettings, tenant_id)
                    assert config is not None
                    config.extra = {**config.extra, "triage": TRIAGE_CONFIG}
                    chunks = (
                        await session.execute(
                            select(Chunk.id, Chunk.chunk_index, Chunk.content).where(
                                Chunk.tenant_id == tenant_id
                            )
                        )
                    ).all()
                    for chunk in chunks:
                        stable_id = uuid.uuid5(
                            uuid.NAMESPACE_URL, f"demo-emporio:{chunk.chunk_index}:{chunk.content}"
                        )
                        await session.execute(
                            update(Chunk)
                            .where(Chunk.tenant_id == tenant_id, Chunk.id == chunk.id)
                            .values(id=stable_id)
                        )
                    await session.commit()
                demos = await _demos(factory, tenant_id)
                service = ChatService(
                    factory,
                    get_settings(),
                    InMemoryVaultStore(),
                    limiter=InMemoryAttemptLimiter(),
                )
                rendered: list[tuple[str, str]] = []
                for demo in demos:
                    lines = [
                        f"# {demo.title}",
                        "",
                        "Mode: deterministic fake model + hash embeddings; not a real LLM.",
                        "Command: `uv run python -m scripts.demo_conversations`",
                        f"Date (UTC): {datetime.now(UTC).date().isoformat()}",
                        "",
                        "Execution: real ChatService, CSV catalog and Empório da Música seed "
                        "policies, disposable database; queries use the local snapshot, "
                        "not a live ERP.",
                        "",
                    ]
                    if demo.note:
                        lines += [demo.note, ""]
                    conversation_id = None
                    for index, turn in enumerate(demo.turns, start=1):
                        async with factory() as session:
                            reply = await service.handle_message(
                                session,
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                text=turn.text,
                                correlation_id=f"demo-{demo.filename}-{index}",
                            )
                        conversation_id = reply.conversation_id
                        lines.append(_render_turn(index, turn, reply))
                    content = "\n".join(lines)
                    if TOKEN_RE.search(content):
                        raise RuntimeError("Demo export refused: unresolved privacy token")
                    rendered.append((demo.filename, content))
            finally:
                await engine.dispose()
        finally:
            await _drop_database(admin, name)
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    paths = []
    for filename, content in rendered:
        path = output_dir / filename
        await asyncio.to_thread(path.write_text, content, encoding="utf-8")
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    configure_logging(Settings(app_env="test", log_level="WARNING"), stream=sys.stderr)
    try:
        paths = asyncio.run(generate_conversations(args.output_dir))
    except Exception as exc:
        print(f"Demo generation failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
