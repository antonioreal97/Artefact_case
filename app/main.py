"""FastAPI application factory.

Middleware (outermost first):
1. ``RequestContextMiddleware`` - correlation id (``X-Request-ID`` in/out; generated when
   absent or not matching ``[A-Za-z0-9._-]{1,64}``), per-request log context, timing ->
   metrics + one ``request.completed`` log line, and the catch-all 500 handler
   (``{"error": "internal_error", "correlation_id": ...}``; never a stack trace).
2. ``BodySizeLimitMiddleware`` - rejects oversized bodies (413) from ``Content-Length`` and
   while streaming: JSON/other 1 MiB, multipart ``MAX_UPLOAD_BYTES`` + 64 KiB envelope,
   text-document endpoint ``MAX_UPLOAD_BYTES``.

Exception handlers render every error as ``{"error", "detail", "correlation_id"}``;
validation errors never echo the submitted input.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from time import perf_counter
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.dependencies import close_shared_clients
from app.api.routes import chat, conversations, handoffs, health, knowledge, metrics
from app.api.webhooks import whatsapp
from app.core.config import Settings, get_settings
from app.core.logging import bind_correlation_id, clear_context, configure_logging, get_logger
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
JSON_BODY_LIMIT = 1024 * 1024
MULTIPART_ENVELOPE = 64 * 1024


def _error_body(error: str, correlation_id: str | None, detail: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": error}
    if detail is not None:
        body["detail"] = detail
    body["correlation_id"] = correlation_id
    return body


def _correlation_id(request: Request) -> str | None:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else None


_SLUG_OVERRIDES = {413: "payload_too_large", 422: "unprocessable_entity"}


def _error_slug(status_code: int) -> str:
    if status_code in _SLUG_OVERRIDES:
        return _SLUG_OVERRIDES[status_code]
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        return "error"
    return re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        clear_context()
        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        correlation_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        bind_correlation_id(correlation_id)

        started = perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            log.error("request.unhandled_error", exc_info=exc)
            if response_started:
                raise
            response = JSONResponse(_error_body("internal_error", correlation_id), status_code=500)
            await response(scope, receive, send_wrapper)
        finally:
            elapsed = perf_counter() - started
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"]
            HTTP_REQUESTS.inc(method=method, route=route, status=str(status_code))
            HTTP_LATENCY.observe(elapsed, method=method, route=route)
            log.info(
                "request.completed",
                method=method,
                route=route,
                status_code=status_code,
                duration_ms=round(elapsed * 1000, 1),
            )
            clear_context()


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    def _limit(self, scope: Scope, headers: Headers) -> int:
        if headers.get("content-type", "").lower().startswith("multipart/form-data"):
            return self.settings.max_upload_bytes + MULTIPART_ENVELOPE
        if str(scope.get("path", "")).endswith("/knowledge/documents/text"):
            return self.settings.max_upload_bytes
        return JSON_BODY_LIMIT

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        limit = self._limit(scope, headers)
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            correlation_id = scope.get("state", {}).get("correlation_id")
            response = JSONResponse(
                _error_body("payload_too_large", correlation_id, "Request body too large"),
                status_code=413,
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(status_code=413, detail="Request body too large")
            return message

        await self.app(scope, limited_receive, send)


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else None
    return JSONResponse(
        _error_body(_error_slug(exc.status_code), _correlation_id(request), detail),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only location, message and type: never the submitted input or ctx values.
    errors = [
        {
            "loc": [str(part) for part in err.get("loc", ())],
            "msg": err.get("msg"),
            "type": err.get("type"),
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        _error_body("validation_error", _correlation_id(request), errors), status_code=422
    )


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info("app.started")
    try:
        yield
    finally:
        await close_shared_clients()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    app = FastAPI(
        title="Empório Agent Platform API",
        version="0.2.0",
        description=(
            "Multi-tenant customer-service agent API. Authenticate with `X-API-Key`; the "
            "tenant is derived from the key only. Errors use "
            '`{"error", "detail", "correlation_id"}`; send `X-Request-ID` to correlate.'
        ),
        lifespan=_lifespan,
    )
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)  # added last = outermost

    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(knowledge.router)
    app.include_router(handoffs.router)
    app.include_router(whatsapp.router)
    return app


app = create_app()
