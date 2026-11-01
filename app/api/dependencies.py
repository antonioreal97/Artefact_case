"""FastAPI dependencies: API-key auth, scopes, and pluggable services.

Authentication: ``X-API-Key: emp_<prefix>_<secret>``. The tenant is derived ONLY from the
credential; any ``tenant_id`` in a body, query string or message text is ignored.
Every rejection returns the same generic 401. Failed authentications are counted per client
IP (``AttemptLimiter``, review M-8): above ``auth_failure_limit_per_minute`` the IP gets 429,
and at most ONE ``auth.failed`` ``AuditEvent`` (``tenant_id=None``, data holds at most the
key prefix - never key material) is written per IP per minute. If the limiter (Redis) is
down, requests are allowed and the failure is logged (availability); only the order-lookup
lockout fails closed.

Service dependencies (chat, ingestion, Redis, WhatsApp client) are separate functions so
tests can replace them with ``app.dependency_overrides``.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, NoReturn, Protocol

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session, get_sessionmaker
from app.core.logging import bind_tenant_id, get_logger
from app.core.metrics import AUTH_FAILURES
from app.core.security import parse_key_prefix, verify_api_key
from app.integrations.meta_whatsapp import MetaWhatsAppClient
from app.models import ApiKey, AuditEvent, Tenant
from app.models.base import utcnow
from app.rag.types import IngestionReceipt
from app.schemas.chat import ChatReply
from app.services.attempt_limiter import AttemptLimiter
from app.services.attempt_limiter_redis import RedisAttemptLimiter

log = get_logger(__name__)

SCOPES = ("chat", "knowledge:write", "knowledge:read", "handoffs", "conversations:read")
RATE_WINDOW_SECONDS = 60

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Tenant API key (`emp_<prefix>_<secret>`). The tenant is derived from it.",
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    api_key_id: uuid.UUID
    scopes: frozenset[str]


_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing API key",
    headers={"WWW-Authenticate": "ApiKey"},
)


def too_many_requests() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests",
        headers={"Retry-After": str(RATE_WINDOW_SECONDS)},
    )


# --- rate limiting ---------------------------------------------------------------------


def get_attempt_limiter(settings: SettingsDep) -> AttemptLimiter:
    """Process-wide Redis limiter (tests override it with ``InMemoryAttemptLimiter``)."""
    if "limiter" not in _shared:
        _shared["limiter"] = RedisAttemptLimiter.from_settings(settings)
    limiter: AttemptLimiter = _shared["limiter"]
    return limiter


LimiterDep = Annotated[AttemptLimiter, Depends(get_attempt_limiter)]


def opaque_ref(settings: Settings, kind: str, value: str) -> str:
    """HMAC-SHA256(user_ref_key, kind:value): stable, non-reversible key material."""
    key = settings.user_ref_key.get_secret_value().encode("utf-8")
    return hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def client_ip(request: Request) -> str:
    # The direct peer only: X-Forwarded-For is client-controlled unless a trusted proxy
    # rewrites it (configure uvicorn --forwarded-allow-ips for that deployment).
    return request.client.host if request.client else "unknown"


async def count_hit(limiter: AttemptLimiter, key: str, *, failure: bool = False) -> int | None:
    """Count one request/failure in the current window; ``None`` if the limiter is down
    (callers then allow the request: availability over throttling)."""
    try:
        if failure:
            return await limiter.record_failure(key, RATE_WINDOW_SECONDS)
        return await limiter.hit(key, RATE_WINDOW_SECONDS)
    except Exception as exc:
        log.warning("rate_limit.unavailable", error_type=type(exc).__name__)
        return None


async def _current_failures(limiter: AttemptLimiter, key: str) -> int:
    try:
        return await limiter.failures(key)
    except Exception as exc:
        log.warning("rate_limit.unavailable", error_type=type(exc).__name__)
        return 0


def _auth_failure_key(settings: Settings, request: Request) -> str:
    return f"authfail:global:ip:{opaque_ref(settings, 'ip', client_ip(request))[:32]}"


async def _reject(
    session: AsyncSession,
    request: Request,
    settings: Settings,
    limiter: AttemptLimiter,
    reason: str,
    prefix: str | None,
) -> NoReturn:
    AUTH_FAILURES.inc(reason=reason)
    log.warning("auth.failed", reason=reason, key_prefix=prefix)
    failures = await count_hit(limiter, _auth_failure_key(settings, request), failure=True)
    # At most one audit row per IP per window (None: limiter down -> keep auditing).
    if failures is None or failures == 1:
        session.add(
            AuditEvent(
                tenant_id=None,
                event_type="auth.failed",
                actor=None,
                correlation_id=getattr(request.state, "correlation_id", None),
                data={"reason": reason, "key_prefix": prefix, "route": request.url.path[:200]},
            )
        )
        await session.commit()
    raise _UNAUTHORIZED


async def get_principal(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    limiter: LimiterDep,
    api_key: Annotated[str | None, Security(_api_key_header)] = None,
) -> Principal:
    failure_key = _auth_failure_key(settings, request)
    if await _current_failures(limiter, failure_key) >= settings.auth_failure_limit_per_minute:
        AUTH_FAILURES.inc(reason="rate_limited")
        log.warning("auth.rate_limited")
        raise too_many_requests()
    if not api_key:
        await _reject(session, request, settings, limiter, "missing", None)
    prefix = parse_key_prefix(api_key)
    if prefix is None:
        await _reject(session, request, settings, limiter, "malformed", None)
    row = (
        await session.execute(
            select(ApiKey, Tenant.status)
            .join(Tenant, Tenant.id == ApiKey.tenant_id)
            .where(ApiKey.key_prefix == prefix)
        )
    ).first()
    if row is None:
        await _reject(session, request, settings, limiter, "unknown", prefix)
    key, tenant_status = row
    if not verify_api_key(api_key, key.key_hash, settings):
        await _reject(session, request, settings, limiter, "bad_secret", prefix)
    if key.revoked_at is not None:
        await _reject(session, request, settings, limiter, "revoked", prefix)
    if tenant_status != "active":
        await _reject(session, request, settings, limiter, "tenant_inactive", prefix)

    key.last_used_at = utcnow()
    await session.commit()
    bind_tenant_id(key.tenant_id)
    return Principal(tenant_id=key.tenant_id, api_key_id=key.id, scopes=frozenset(key.scopes))


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_scope(scope: str) -> Callable[[Principal], Awaitable[Principal]]:
    """Dependency factory: authenticated principal that must hold ``scope`` (else 403)."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")

    async def _check(principal: PrincipalDep) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient scope")
        return principal

    return _check


# --- pluggable services -------------------------------------------------------------------


# Process-wide clients/services, created lazily and closed by the app lifespan.
_shared: dict[str, Any] = {}


class ChatHandler(Protocol):
    async def handle_message(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        text: str,
        channel: str = "api",
        external_user_ref: str | None = None,
        correlation_id: str,
    ) -> ChatReply: ...


class VersionCreator(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        title: str,
        data: bytes,
        filename: str | None,
        source_type: str,
        category: str | None = None,
        source_url: str | None = None,
        document_id: uuid.UUID | None = None,
    ) -> IngestionReceipt: ...


IngestionEnqueuer = Callable[[uuid.UUID, uuid.UUID], Awaitable[None]]


def get_chat_service(settings: SettingsDep, limiter: LimiterDep) -> ChatHandler:
    """Process-wide ``ChatService`` (Task E) with a Redis-backed privacy vault store and the
    shared Redis attempt limiter (cross-conversation order-lookup lockout, review H-1)."""
    if "chat" not in _shared:
        from app.services.chat import ChatService
        from app.services.vault_store import RedisVaultStore

        vault_store = RedisVaultStore(settings)
        _shared["vault_store"] = vault_store
        _shared["chat"] = ChatService(get_sessionmaker(), settings, vault_store, limiter=limiter)
    service: ChatHandler = _shared["chat"]
    return service


def get_version_creator() -> VersionCreator:
    from app.rag.ingestion import create_document_version

    return create_document_version


def get_ingestion_enqueuer() -> IngestionEnqueuer:
    from app.services.ingestion_jobs import enqueue_ingestion

    return enqueue_ingestion


def get_redis(settings: SettingsDep) -> Any:
    """Redis client (readiness check, webhook dedupe). Typed ``Any`` so fakes can stand in."""
    if "redis" not in _shared:
        _shared["redis"] = Redis.from_url(
            settings.redis_url, socket_connect_timeout=1, socket_timeout=1
        )
    return _shared["redis"]


def get_whatsapp_client(settings: SettingsDep) -> MetaWhatsAppClient:
    if "whatsapp" not in _shared:
        _shared["whatsapp"] = MetaWhatsAppClient(settings)
    client: MetaWhatsAppClient = _shared["whatsapp"]
    return client


async def close_shared_clients() -> None:
    redis = _shared.pop("redis", None)
    if redis is not None:
        await redis.aclose()
    whatsapp = _shared.pop("whatsapp", None)
    if whatsapp is not None:
        await whatsapp.aclose()
    vault_store = _shared.pop("vault_store", None)
    if vault_store is not None:
        await vault_store.aclose()
    limiter = _shared.pop("limiter", None)
    if isinstance(limiter, RedisAttemptLimiter):
        await limiter.aclose()
    _shared.clear()


ChatServiceDep = Annotated[ChatHandler, Depends(get_chat_service)]
VersionCreatorDep = Annotated[VersionCreator, Depends(get_version_creator)]
EnqueuerDep = Annotated[IngestionEnqueuer, Depends(get_ingestion_enqueuer)]
RedisDep = Annotated[Any, Depends(get_redis)]
WhatsAppClientDep = Annotated[MetaWhatsAppClient, Depends(get_whatsapp_client)]
