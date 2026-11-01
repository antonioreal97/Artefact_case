"""Exercise the real CLI against an empty database, never the compose data volume."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.database import Base


@pytest.mark.integration
async def test_initial_migration_matches_models(db_engine: AsyncEngine) -> None:
    name = f"test_migrations_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(
        db_engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    engine = create_async_engine(db_engine.url.set(database=name))
    created = False
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        env = {
            **os.environ,
            "DATABASE_URL": engine.url.render_as_string(hide_password=False),
            "APP_ENV": "test",
            "LLM_PROVIDER": "fake",
            "EMBEDDING_PROVIDER": "hash",
        }
        for args in [("upgrade", "head"), ("check",)]:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "alembic",
                *args,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await process.communicate()
            assert process.returncode == 0, output.decode()
            if args == ("check",):
                assert b"No new upgrade operations detected" in output
        async with engine.connect() as conn:
            tables = set(
                await conn.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
            assert tables == set(Base.metadata.tables) | {"alembic_version"}
            indexes: dict[str, str] = dict(
                (
                    await conn.execute(
                        text(
                            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunks'"
                        )
                    )
                ).all()
            )
            assert "USING hnsw (embedding vector_cosine_ops)" in indexes["ix_chunks_embedding_hnsw"]
            assert "USING gin (search_vector)" in indexes["ix_chunks_search_vector"]
            expression = await conn.scalar(
                text(
                    "SELECT generation_expression FROM information_schema.columns "
                    "WHERE table_name = 'chunks' AND column_name = 'search_vector'"
                )
            )
            assert "to_tsvector('portuguese'::regconfig, content)" == expression
    finally:
        await engine.dispose()
        if created:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
