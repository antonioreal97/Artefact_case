"""Shared fixtures for the platform test-suite.

Integration tests use a throw-away database (``test_<random>``) created on the compose
Postgres (``docker compose up -d db``) so parallel runs never collide. If Postgres is not
reachable, tests marked ``integration`` are skipped with an explicit reason.
Override the server with TEST_DATABASE_ADMIN_URL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("INGESTION_MODE", "inline")

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL", "postgresql+asyncpg://emporio:emporio@localhost:5434/postgres"
)


@pytest.fixture(scope="session")
async def db_engine() -> AsyncIterator[AsyncEngine]:
    import app.models  # noqa: F401  (register all tables)
    from app.core.database import Base

    name = f"test_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - environment dependent
        await admin.dispose()
        pytest.skip(f"Postgres not reachable for integration tests: {type(exc).__name__}")
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


@pytest.fixture
async def two_tenants(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Two fresh tenants (unique slugs per test). Tenant B is synthetic, test-only."""
    from app.models import AgentSettings, Tenant

    suffix = uuid.uuid4().hex[:8]
    a = Tenant(slug=f"emporio-{suffix}", name="Empório da Música")
    b = Tenant(slug=f"other-{suffix}", name="Loja Sintética de Testes")
    async with session_factory() as s:
        s.add_all([a, b])
        await s.flush()
        s.add_all(
            [
                AgentSettings(tenant_id=a.id, assistant_name="Assistente Empório"),
                AgentSettings(tenant_id=b.id, assistant_name="Assistente Sintético"),
            ]
        )
        await s.commit()
    return a.id, b.id
