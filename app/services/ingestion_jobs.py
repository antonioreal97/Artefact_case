"""arq worker entry point and inline/queue dispatch.

The worker also runs the daily retention purge (``app.services.retention``, review M-10)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings
from arq.cron import CronJob, cron

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.rag.embeddings import get_embedding_provider
from app.rag.ingestion import process_document_version
from app.services.retention import purge_expired


async def ingest_document_version(
    ctx: dict[str, Any], tenant_id: str | UUID, version_id: str | UUID
) -> None:
    settings = get_settings()
    factory = ctx.get("session_factory") or get_sessionmaker()
    embedder = ctx.get("embedder") or get_embedding_provider(settings)
    async with factory() as session:
        await process_document_version(
            session,
            tenant_id=UUID(str(tenant_id)),
            version_id=UUID(str(version_id)),
            embedder=embedder,
        )


async def purge_retention(ctx: dict[str, Any]) -> dict[str, int]:
    """Daily cron: delete conversations/audit rows past ``retention_days``."""
    factory = ctx.get("session_factory") or get_sessionmaker()
    report = await purge_expired(factory, get_settings())
    return {
        "tenants": report.tenants,
        "conversations": report.conversations,
        "audit_events": report.audit_events,
        "raw_contents_cleared": report.raw_contents_cleared,
    }


async def enqueue_ingestion(tenant_id: UUID, version_id: UUID) -> None:
    settings = get_settings()
    if settings.ingestion_mode == "inline":
        await ingest_document_version({}, tenant_id, version_id)
        return
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await redis.enqueue_job(
            "ingest_document_version",
            str(tenant_id),
            str(version_id),
            _job_id=f"ingest:{tenant_id}:{version_id}",
        )
    finally:
        await redis.aclose()


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Awaitable[None]]]] = [ingest_document_version]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    cron_jobs: ClassVar[list[CronJob]] = [
        cron(purge_retention, hour={3}, minute={17}, unique=True, timeout=1800)
    ]
    job_timeout = 300
    keep_result = 0  # allow explicit reprocessing immediately after completion
