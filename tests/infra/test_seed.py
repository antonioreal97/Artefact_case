from __future__ import annotations

import csv
import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import generate_api_key, hash_api_key
from app.models import (
    AgentSettings,
    ApiKey,
    Chunk,
    CommerceOrderItem,
    CommerceProduct,
    Document,
    DocumentVersion,
    TenantChannel,
)
from scripts.commerce import DATA_DIR, EXPORTS, import_commerce, load_commerce
from scripts.create_tenant import PUBLIC_CONTACT_VALUES, SCOPES, api_key_from_env, create_tenant
from scripts.seed import seed

EXPECTED_COUNTS = {
    "categories": 9,
    "products": 65,
    "promotions": 25,
    "customers": 50,
    "orders": 20,
    "order_items": 22,
}


@pytest.mark.integration
async def test_seed_counts_idempotency_and_policy(
    session_factory: async_sessionmaker[AsyncSession], capsys: pytest.CaptureFixture[str]
) -> None:
    assert get_settings().embedding_provider == "hash"
    snapshots: list[dict[str, set[uuid.UUID]]] = []
    tenant_ids: list[uuid.UUID] = []
    for run in range(2):
        async with session_factory() as session:
            tenant_id, plaintext, counts = await seed(
                session, whatsapp_phone_number_id="synthetic-seed-channel"
            )
            tenant_ids.append(tenant_id)
            assert counts == EXPECTED_COUNTS
            output = capsys.readouterr().out
            if run == 0:
                assert plaintext is not None
                assert output.strip() == plaintext
                credential = await session.scalar(
                    select(ApiKey).where(ApiKey.tenant_id == tenant_id)
                )
                assert credential is not None
                pepper = get_settings().api_key_pepper.get_secret_value()
                assert (
                    credential.key_hash == hashlib.sha256((pepper + plaintext).encode()).hexdigest()
                )
                assert credential.key_prefix == plaintext.split("_", 2)[1]
                assert credential.scopes == SCOPES
            else:
                assert plaintext is None
                assert output == ""
            ids: dict[str, set[uuid.UUID]] = {}
            for name, (_, model) in EXPORTS.items():
                table = model.__table__
                rows = set(
                    await session.scalars(select(table.c.id).where(table.c.tenant_id == tenant_id))
                )
                assert len(rows) == EXPECTED_COUNTS[name]
                ids[name] = rows
            snapshots.append(ids)
            for model in (ApiKey, AgentSettings, TenantChannel, Document, DocumentVersion):
                assert (
                    await session.scalar(
                        select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
                    )
                    == 1
                )
            config = await session.scalar(
                select(AgentSettings).where(AgentSettings.tenant_id == tenant_id)
            )
            assert config is not None
            assert config.timezone == "America/Campo_Grande"
            assert config.default_language == "pt-BR"
            assert config.public_contact_values == PUBLIC_CONTACT_VALUES
            document = await session.scalar(select(Document).where(Document.tenant_id == tenant_id))
            version = await session.scalar(
                select(DocumentVersion).where(DocumentVersion.tenant_id == tenant_id)
            )
            assert document is not None and version is not None
            assert document.current_version_id == version.id
            assert version.ingestion_status == "completed"
            assert version.chunk_count and version.chunk_count > 0
            assert (
                await session.scalar(
                    select(func.count()).select_from(Chunk).where(Chunk.tenant_id == tenant_id)
                )
                == version.chunk_count
            )
    assert tenant_ids[0] == tenant_ids[1]
    assert snapshots[0] == snapshots[1]


@pytest.mark.integration
async def test_commerce_updates_only_selected_tenant(
    session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_tenants
    exports = load_commerce()
    for tenant_id in (a, b):
        await import_commerce(session, tenant_id=tenant_id, exports=exports)
    await session.commit()
    exports["products"][0]["stock_quantity"] = 987
    exports["order_items"][0]["quantity"] = 7
    await import_commerce(session, tenant_id=a, exports=exports)
    await session.commit()
    for tenant_id, expected_stock in ((a, 987), (b, 12)):
        stock = await session.scalar(
            select(CommerceProduct.stock_quantity).where(
                CommerceProduct.tenant_id == tenant_id,
                CommerceProduct.external_id == exports["products"][0]["external_id"],
            )
        )
        assert stock == expected_stock
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CommerceOrderItem)
                .where(CommerceOrderItem.tenant_id == tenant_id)
            )
            == 22
        )
        quantity = await session.scalar(
            select(CommerceOrderItem.quantity).where(
                CommerceOrderItem.tenant_id == tenant_id,
                CommerceOrderItem.order_external_id
                == exports["order_items"][0]["order_external_id"],
                CommerceOrderItem.product_external_id
                == exports["order_items"][0]["product_external_id"],
            )
        )
        assert quantity == (7 if tenant_id == a else load_commerce()["order_items"][0]["quantity"])


@pytest.mark.integration
async def test_environment_key_not_echoed_or_rotated(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("scripts.create_tenant.TENANT_SLUG", f"seed-auth-{uuid.uuid4().hex[:8]}")
    fresh, _ = generate_api_key()
    monkeypatch.setenv("INFRA_TEST_API_KEY", fresh)
    assert api_key_from_env("INFRA_TEST_API_KEY") == fresh
    tenant_id, plaintext = await create_tenant(session, api_key=fresh)
    assert plaintext is None  # Environment-supplied keys are never echoed.
    await session.commit()
    assert await create_tenant(session, api_key=fresh) == (tenant_id, None)
    credential = await session.scalar(select(ApiKey).where(ApiKey.tenant_id == tenant_id))
    assert credential is not None
    original_hash = credential.key_hash
    assert original_hash == hash_api_key(fresh)
    different, _ = generate_api_key()
    with pytest.raises(ValueError, match="different seed key"):
        await create_tenant(session, api_key=different)
    await session.rollback()
    assert (
        await session.scalar(select(ApiKey.key_hash).where(ApiKey.tenant_id == tenant_id))
        == original_hash
    )
    assert fresh not in capsys.readouterr().out


def test_csv_counts_and_boolean_conversion() -> None:
    exports = load_commerce()
    assert {name: len(rows) for name, rows in exports.items()} == EXPECTED_COUNTS
    assert {type(row["is_active"]) for row in exports["promotions"]} == {bool}
    assert {row["is_active"] for row in exports["promotions"]} == {False, True}


@pytest.mark.parametrize(
    ("table", "field", "value"),
    [
        ("products", "status", "invalid"),
        ("products", "specs", "not json"),
        ("products", "specs", "[]"),
        ("products", "specs", '{"size": NaN}'),
        ("products", "price_brl", "NaN"),
        ("products", "price_brl", "12.345"),
        ("products", "category_id", "999999"),
        ("products", "stock_quantity", "-1"),
        ("orders", "customer_id", "999999"),
        ("orders", "status", "invalid"),
        ("orders", "payment_method", "invalid"),
        ("order_items", "product_id", "999999"),
        ("order_items", "quantity", "0"),
        ("promotions", "is_active", "true"),
        ("promotions", "discount_percent", "101"),
    ],
)
def test_invalid_csv_rejected_without_row_values(
    tmp_path: Path, table: str, field: str, value: str
) -> None:
    shutil.copytree(DATA_DIR, tmp_path, dirs_exist_ok=True)
    path = tmp_path / f"desafio_tecnico_ai_eng - {table}.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows: list[dict[str, Any]] = list(csv.DictReader(stream))
    rows[0][field] = value
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError) as error:
        load_commerce(tmp_path)
    assert "CSV" in str(error.value)
    assert "customer_id=" not in str(error.value)


@pytest.mark.parametrize("table", list(EXPORTS))
def test_duplicate_csv_key_rejected(tmp_path: Path, table: str) -> None:
    shutil.copytree(DATA_DIR, tmp_path, dirs_exist_ok=True)
    path = tmp_path / f"desafio_tecnico_ai_eng - {table}.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows([*rows, rows[0]])
    with pytest.raises(ValueError, match="Duplicate key"):
        load_commerce(tmp_path)


@pytest.mark.integration
async def test_seed_welcome_defaults_and_preserves_operator_value(session, monkeypatch):
    from scripts.create_tenant import HANDOFF_MESSAGE, WELCOME_MESSAGE

    monkeypatch.setattr("scripts.create_tenant.TENANT_SLUG", f"welcome-{uuid.uuid4().hex}")
    tenant, _ = await create_tenant(session)
    await session.commit()
    config = await session.get(AgentSettings, tenant)
    assert config.welcome_message == WELCOME_MESSAGE
    assert config.handoff_message == HANDOFF_MESSAGE
    config.welcome_message = "Olá! Seja bem-vindo à nossa loja."
    config.handoff_message = "Um momento, já te passo."
    await session.commit()
    await create_tenant(session)
    assert config.welcome_message == "Olá! Seja bem-vindo à nossa loja."
    assert config.handoff_message == "Um momento, já te passo."
    await create_tenant(session, refresh_welcome_and_handoff=True)
    assert config.welcome_message == WELCOME_MESSAGE
    assert config.handoff_message == HANDOFF_MESSAGE
    for empty in (None, "", "   "):
        config.welcome_message = empty
        await session.commit()
        await create_tenant(session)
        assert config.welcome_message == WELCOME_MESSAGE
        await session.commit()


async def test_triage_seed_requires_explicit_refresh(session, monkeypatch):
    from scripts.create_tenant import TRIAGE_CONFIG

    monkeypatch.setattr("scripts.create_tenant.TENANT_SLUG", f"triage-{uuid.uuid4().hex}")
    tenant, _ = await create_tenant(session)
    config = await session.get(AgentSettings, tenant)
    assert config.extra["triage"] == TRIAGE_CONFIG
    config.extra = {"operator": "keep", "triage": {"enabled": False}}
    await session.commit()
    await create_tenant(session)
    assert config.extra == {"operator": "keep", "triage": {"enabled": False}}
    config.extra = {"operator": "keep"}  # Pre-triage tenant: ordinary seed must not opt in.
    await session.commit()
    await create_tenant(session)
    assert config.extra == {"operator": "keep"}
    await create_tenant(session, refresh_triage=True)
    assert config.extra == {"operator": "keep", "triage": TRIAGE_CONFIG}
