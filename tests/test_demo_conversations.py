"""Run the real demo generator against disposable Postgres databases."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from scripts.demo_conversations import generate_conversations

pytestmark = pytest.mark.integration

EXPECTED_FILES = {
    "01-catalogo-violoes-ate-1000.md",
    "02-preco-takamine-gd20.md",
    "03-politica-devolucao.md",
    "04-status-pedido-verificado.md",
    "05-fora-do-escopo-e-endereco.md",
}


async def test_demo_conversations(tmp_path: Path) -> None:
    paths = await generate_conversations(tmp_path / "first")
    assert {path.name for path in paths} == EXPECTED_FILES
    contents = {path.name: path.read_text() for path in paths}
    for content in contents.values():
        assert "fake determinístico + embeddings hash; não é um LLM real" in content
        assert "uv run python -m scripts.demo_conversations" in content
        assert "Data (UTC):" in content
        assert content.count("**Cliente:**") == content.count("**Agente:**") == 2
        assert content.count("- Ferramentas:") == content.count("- Citações:") == 2
        assert content.count("- Handoff:") == 2
        assert "[[" not in content
        assert not re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f-]{22}\b", content)
    catalog = contents["01-catalogo-violoes-ate-1000.md"].split("## Turno 2")[0]
    assert "Quais opções de violões disponíveis custando até R$1000?" in catalog
    product_lines = re.findall(r"^(.+): R\$ ([\d.,]+)\.$", catalog, re.MULTILINE)
    assert product_lines == [
        ("Yamaha C40 Nylon Natural", "599,90"),
        ("Yamaha C70 Nylon Natural", "849,00"),
        ("Tagima Memphis AC-39 Nylon Natural", "429,90"),
        ("Giannini GN-15 Nylon Cedr Natural", "689,00"),
        ("Rozini RC-104 Nylon Estudante", "459,90"),
    ]
    assert all(
        float(price.replace(".", "").replace(",", ".")) <= 1000 for _, price in product_lines
    )
    price = contents["02-preco-takamine-gd20.md"]
    assert "Takamine GD20 Dreadnought Natural: R$ 2.199,00." in price
    assert "disponível em estoque" in price and "check_stock" in price
    policy = contents["03-politica-devolucao.md"]
    for turn in policy.split("## Turno ")[1:]:
        assert "7 (sete) dias" in turn and "embalagem original" in turn
        assert "[S" in turn and "- grounded: true" in turn
    order = contents["04-status-pedido-verificado.md"]
    first, second = order.split("## Turno 2")
    assert "Não foi possível confirmar os dados do pedido" in first
    assert "BRAB1234567BR" not in first and "2025-10-25" not in first
    assert "Situação: entregue" in second
    assert "Rastreamento: BRAB1234567BR" in second
    assert "Previsão de entrega: 2025-10-25" in second
    assert "@" not in order and "pedido 1" not in order
    address, refusal = contents["05-fora-do-escopo-e-endereco.md"].split("## Turno 2")
    assert "Rua 14 de Maio, 3200" in address and "[S" in address
    assert "Não encontrei informação confiável" in refusal and "Handoff: no_information" in refusal
    again = await generate_conversations(tmp_path / "again")

    def without_date(value: str) -> str:
        return re.sub(r"Data \(UTC\): .*", "", value)

    assert {path.name: without_date(path.read_text()) for path in again} == {
        name: without_date(content) for name, content in contents.items()
    }


async def test_generator_drops_database_after_seed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from scripts import demo_conversations as demo
    from scripts.eval_rag import _drop_database

    dropped: list[bool] = []

    async def fail_seed(factory: async_sessionmaker[AsyncSession]) -> None:
        raise RuntimeError("synthetic seed failure")

    async def checked_drop(admin_url: str, name: str) -> None:
        await _drop_database(admin_url, name)
        engine = create_async_engine(admin_url)
        try:
            async with engine.connect() as conn:
                exists = await conn.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :name)"),
                    {"name": name},
                )
                dropped.append(not exists)
        finally:
            await engine.dispose()

    monkeypatch.setattr(demo, "seed_tenants", fail_seed)
    monkeypatch.setattr(demo, "_drop_database", checked_drop)
    with pytest.raises(RuntimeError, match="synthetic seed failure"):
        await demo.generate_conversations(tmp_path)
    assert dropped == [True]
    assert not await asyncio.to_thread(lambda: list(tmp_path.glob("*.md")))
