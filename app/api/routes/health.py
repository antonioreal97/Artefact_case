"""Liveness/readiness probes (unauthenticated; responses never include config or secrets)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.dependencies import RedisDep, SessionDep
from app.core.logging import get_logger
from app.schemas.api import HealthOut, ReadinessOut

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/health", response_model=HealthOut)
async def liveness() -> HealthOut:
    """Process is up. Example response: `{"status": "ok"}`."""
    return HealthOut()


@router.get(
    "/health/ready",
    response_model=ReadinessOut,
    responses={503: {"model": ReadinessOut, "description": "A dependency is unavailable"}},
)
async def readiness(session: SessionDep, redis: RedisDep) -> JSONResponse:
    """Database and Redis reachable.

    Example: `{"status": "ready", "checks": {"database": "ok", "redis": "ok"}}`
    (503 with `"not_ready"` and the failing check marked `"fail"`)."""
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        log.warning("readiness.database_failed", error_type=type(exc).__name__)
        checks["database"] = "fail"
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        log.warning("readiness.redis_failed", error_type=type(exc).__name__)
        checks["redis"] = "fail"
    ready = all(v == "ok" for v in checks.values())
    body = {"status": "ready" if ready else "not_ready", "checks": checks}
    return JSONResponse(body, status_code=200 if ready else 503)
