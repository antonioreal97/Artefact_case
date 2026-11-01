"""Prometheus scrape endpoint.

Unauthenticated by design (standard scrape pattern): the registry holds only tenant-free
aggregates (route templates, status codes, reasons, error class names). Restrict access
at the network/ingress layer in production. See ``app.core.metrics``."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.metrics import REGISTRY

router = APIRouter(tags=["ops"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    """Prometheus text exposition (format 0.0.4), e.g.
    `http_requests_total{method="POST",route="/v1/chat/messages",status="200"} 3`."""
    return PlainTextResponse(REGISTRY.render(), media_type="text/plain; version=0.0.4")
