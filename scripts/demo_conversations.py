"""Generate five offline conversations: uv run python -m scripts.demo_conversations."""

from __future__ import annotations

import argparse
import asyncio
import os
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
from app.models import Chunk, CommerceCustomer, CommerceOrder
from app.schemas.chat import ChatReply
from app.services.attempt_limiter import InMemoryAttemptLimiter
from app.services.chat import ChatService
from app.services.privacy_vault import TOKEN_RE
from app.services.vault_store import InMemoryVaultStore
from scripts.eval_rag import (
    ADMIN_URL_DEFAULT,
    ROOT,
    _create_database,
    _drop_database,
    mode_environment,
    seed_tenants,
)

DEFAULT_OUTPUT_DIR = ROOT / "examples" / "conversations"


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
            "01-catalogo-violoes-ate-1000.md",
            "Violões disponíveis até R$ 1.000",
            (
                DemoTurn("Quais opções de violões disponíveis custando até R$1000?"),
                DemoTurn("Qual o preço do Yamaha C40?"),
            ),
        ),
        Demo(
            "02-preco-takamine-gd20.md",
            "Preço e estoque do Takamine GD20",
            (
                DemoTurn("Quanto custa o Takamine GD20?"),
                DemoTurn("Takamine GD20 disponível em estoque?"),
            ),
        ),
        Demo(
            "03-politica-devolucao.md",
            "Arrependimento e condições de devolução",
            (
                DemoTurn("Me arrependi da minha compra, posso devolver meu pedido?"),
                DemoTurn("Quais as condições para devolução por arrependimento e a nota fiscal?"),
            ),
            "O modelo determinístico reformula a consulta: arrependi → arrependimento, "
            "devolver → devolução. A pergunta original, sem reformulação, retorna "
            "no_information com embeddings hash; essa limitação de retrieval permanece. "
            "A resposta cita a seção recuperada, sem avaliar a elegibilidade individual.",
        ),
        Demo(
            "04-status-pedido-verificado.md",
            "Pedido: contato incorreto e verificação correta",
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
            "Dados do seed são fictícios. Entradas pessoais foram omitidas deste exemplo; "
            "somente a resposta final ao cliente mostra os detalhes autorizados, "
            "revelados localmente após verificação. Nenhum payload do modelo é exportado.",
        ),
        Demo(
            "05-fora-do-escopo-e-endereco.md",
            "Endereço da loja e pergunta fora do escopo",
            (
                DemoTurn("Qual o endereço da loja?"),
                DemoTurn("Qual é a capital da Islândia?"),
            ),
            "O handoff registra uma solicitação; não comprova que um atendente foi notificado.",
        ),
    ]


def _render_turn(index: int, turn: DemoTurn, reply: ChatReply) -> str:
    sources = (
        "; ".join(
            f"[{source.label}] {source.title}, {source.section or 'sem seção'}, "
            f"página {source.page}"
            for source in reply.sources
        )
        or "nenhuma"
    )
    return "\n".join(
        [
            f"## Turno {index}",
            "",
            f"**Cliente:** {turn.display or turn.text}",
            "",
            *(
                [f"**Agente (boas-vindas):**\n\n{reply.welcome_message}", ""]
                if reply.welcome_message
                else []
            ),
            f"**Agente:**\n\n{reply.reply}",
            "",
            f"- Ferramentas: {', '.join(reply.tools_used) or 'nenhuma'}.",
            f"- Citações: {sources}.",
            f"- Handoff: {reply.handoff.reason if reply.handoff else 'nenhum'}.",
            f"- grounded: {str(reply.grounded).lower()}.",
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
                        "Modo: modelo fake determinístico + embeddings hash; não é um LLM real.",
                        "Comando: `uv run python -m scripts.demo_conversations`",
                        f"Data (UTC): {datetime.now(UTC).date().isoformat()}",
                        "",
                        "Execução: ChatService real, catálogo CSV e políticas do seed Empório da "
                        "Música, banco descartável; consultas ao snapshot local, "
                        "não a um ERP ao vivo.",
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
