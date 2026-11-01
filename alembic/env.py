"""Run migrations with the application's async PostgreSQL settings."""

from __future__ import annotations

import asyncio

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401
from alembic import context
from app.core.config import get_settings
from app.core.database import Base


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=get_settings().database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    # Connection injection lets async integration tests run Alembic via run_sync.
    connection = context.config.attributes.get("connection")
    if connection is not None:
        run_migrations(connection)
    else:
        asyncio.run(run_online())
