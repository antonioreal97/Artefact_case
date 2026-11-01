"""Structured JSON logging with request context and privacy redaction.

Rules (SECURITY.md "Logs and Traces"):
- Every event carries ``correlation_id`` / ``tenant_id`` / ``conversation_id`` from
  contextvars when they are bound.
- Every value (except a small allowlist of structural identifiers) goes through
  ``emporio_agent.privacy.redact_for_log``.
- Keys that would carry bodies, prompts, messages or credentials are dropped wholesale.
- Exceptions are logged as type + stack frames only: exception *messages* are omitted
  because driver/library errors routinely embed bound parameters (customer data).
"""

from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any, TextIO

import structlog

from app.core.config import Settings
from emporio_agent.privacy import redact_for_log

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
conversation_id_var: ContextVar[str | None] = ContextVar("conversation_id", default=None)

# Structural values that are safe and must not be mangled by the PII regexes (UUIDs and
# hex digests can look like phone numbers / postal codes to the scanner).
_SAFE_KEYS = frozenset(
    {
        "event",
        "level",
        "timestamp",
        "logger",
        "correlation_id",
        "tenant_id",
        "conversation_id",
        "message_id",
        "document_id",
        "version_id",
        "handoff_id",
        "api_key_id",
        "key_prefix",
        "method",
        "route",
        "status_code",
        "duration_ms",
        "exc_type",
        "stack",
    }
)
# Never log these, whatever their content.
_FORBIDDEN_KEYS = frozenset(
    {
        "body",
        "request_body",
        "response_body",
        "prompt",
        "messages",
        "message",
        "text",
        "content",
        "reply",
        "api_key",
        "x-api-key",
        "authorization",
        "token",
        "access_token",
        "verify_token",
        "secret",
        "password",
        "wa_id",
    }
)


def bind_correlation_id(value: str | None) -> None:
    correlation_id_var.set(value)


def bind_tenant_id(value: object | None) -> None:
    tenant_id_var.set(str(value) if value is not None else None)


def bind_conversation_id(value: object | None) -> None:
    conversation_id_var.set(str(value) if value is not None else None)


def clear_context() -> None:
    correlation_id_var.set(None)
    tenant_id_var.set(None)
    conversation_id_var.set(None)


def _add_context(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, var in (
        ("correlation_id", correlation_id_var),
        ("tenant_id", tenant_id_var),
        ("conversation_id", conversation_id_var),
    ):
        value = var.get()
        if value is not None:
            event_dict.setdefault(key, value)
    return event_dict


def _sanitize_exception(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    exc_info = event_dict.pop("exc_info", None)
    exc: BaseException | None = None
    if isinstance(exc_info, BaseException):
        exc = exc_info
    elif isinstance(exc_info, tuple) and len(exc_info) == 3:
        exc = exc_info[1]
    elif exc_info is True:
        exc = sys.exc_info()[1]
    if exc is not None:
        event_dict["exc_type"] = type(exc).__name__
        event_dict["stack"] = [
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)
        ][-15:]
    return event_dict


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    to_scan: dict[str, Any] = {}
    for key in list(event_dict):
        folded = key.casefold()
        if folded in _FORBIDDEN_KEYS:
            event_dict[key] = "[OMITTED]"
        elif folded not in _SAFE_KEYS:
            to_scan[key] = event_dict[key]
    # As one mapping so key-based rules (email, phone, order_id ...) apply at top level too.
    event_dict.update(redact_for_log(to_scan))
    event = event_dict.get("event")
    if isinstance(event, str):
        event_dict["event"] = redact_for_log(event)
    return event_dict


def configure_logging(settings: Settings, stream: TextIO | None = None) -> None:
    """Configure structlog (JSON to ``stream``/stdout) and quiet leaky stdlib loggers."""
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_context,
            _sanitize_exception,
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.WriteLoggerFactory(file=stream or sys.stdout),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(level=level, format="%(levelname)s %(name)s %(message)s")
    # Uvicorn access lines contain raw query strings (e.g. hub.verify_token); the request
    # middleware logs route templates instead. httpx would log Graph API URLs.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
