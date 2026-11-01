"""Order status tool (RESTRICTED data, trusted local boundary).

The model only ever handles vault tokens (``[[ORDER_1]]``, ``[[EMAIL_1]]``/``[[PHONE_1]]``).
Tokens are resolved locally, identity is verified deterministically and the model gets an
abstract ``OrderStatusView`` whose restricted details are new vault tokens
(``[[TRACKING_n]]``, ``[[DATE_n]]``) that trusted code reveals in the final answer.

No enumeration: a nonexistent order and an identity mismatch return the SAME failure.

Brute force (review H-1): failed verifications are counted by ``ctx.limiter`` (Redis at
runtime, shared across conversations and processes) under two keys:

* per target - ``orderfail:{tenant}:order:{sha256(order_id)}``
* per actor  - ``orderfail:{tenant}:actor:{ctx.actor_ref or conversation_id}``

Every failure increments BOTH. When either reaches ``order_lookup_max_failures`` inside
``order_lookup_lockout_seconds`` every lookup for that order/actor is refused with
``not_authorized`` + ``MSG_LOCKED`` and ``result_summary["locked"] = True`` (the agent maps
that to a ``privacy_guard`` handoff). A later success never resets a counter (that would let
an attacker interleave one known order with guesses). Limiter errors fail CLOSED
(``unavailable``). Each failed verification writes an ``order.verification_failed`` audit
event without PII (no order id, no contact; only a hash of the actor reference).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.integrations.base import OrderRecord
from app.models import AuditEvent
from app.schemas.tools import ToolResult
from app.services.privacy_vault import TOKEN_RE
from app.tools.catalog import MSG_UNAVAILABLE, run_tool
from app.tools.context import ToolContext

OrderStatus = Literal["pending", "confirmed", "shipped", "delivered", "cancelled", "unknown"]
_KNOWN_STATUSES: frozenset[str] = frozenset(
    {"pending", "confirmed", "shipped", "delivered", "cancelled"}
)

AUDIT_EVENT_TYPE = "order.verification_failed"

MSG_NOT_AUTHORIZED = (
    "Não foi possível confirmar os dados do pedido. Verifique o número do pedido e o "
    "e-mail ou telefone usado na compra."
)
MSG_LOCKED = (
    "Por segurança, não posso consultar este pedido agora, após várias tentativas sem "
    "confirmação. Posso encaminhar você para um atendente humano."
)
MSG_BAD_ORDER_REF = "Não consegui identificar o número do pedido. Pode informá-lo novamente?"
MSG_BAD_CONTACT = (
    "Para consultar o pedido preciso do e-mail ou telefone usado na compra. Pode informá-lo?"
)

_DUMMY = "\x00dummy-contact-for-constant-time\x00"


class _LimiterUnavailableError(Exception):
    """The failure counter could not be read/written: refuse the lookup (fail closed)."""


class LookupOrderInput(BaseModel):
    order_token: str = Field(min_length=1, max_length=64)
    contact_token: str = Field(min_length=1, max_length=64)


class OrderStatusView(BaseModel):
    """Abstract, tokenized view. Never holds the raw order id, tracking code, dates or
    customer data – only tokens that trusted code reveals after generation."""

    identity_verified: bool
    status: OrderStatus
    has_tracking: bool
    tracking_token: str | None = None
    estimated_delivery_token: str | None = None
    order_token: str


def _safe_token(value: object) -> str:
    """Audit-safe form of a token argument: the token itself, or a placeholder if the
    caller passed something else (possibly a raw value)."""
    if isinstance(value, str) and TOKEN_RE.fullmatch(value.strip()):
        return value.strip()
    return "[NOT_A_TOKEN]"


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _national_digits(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) in (12, 13) and digits.startswith("55"):
        digits = digits[2:]  # +55 country code
    if len(digits) in (11, 12) and digits.startswith("0"):
        digits = digits[1:]  # trunk prefix (0XX)
    return digits


def _phone_matches(supplied: str, stored: str) -> bool:
    """Full national numbers only (review L-3): area code + 8 or 9 digits on BOTH sides.

    Tolerates formatting, ``+55`` and the trunk ``0``; the only accepted difference is the
    legacy 8-digit form of a 9-digit mobile number (same area code, leading 9 omitted)."""
    s, t = _national_digits(supplied), _national_digits(stored)
    if len(s) not in (10, 11) or len(t) not in (10, 11):
        _equal(_DUMMY, _DUMMY)
        return False
    if len(s) != len(t):
        longer, shorter = (s, t) if len(s) == 11 else (t, s)
        if longer[2] != "9":
            _equal(_DUMMY, _DUMMY)
            return False
        s, t = longer[:2] + longer[3:], shorter
    return _equal(s, t)


def _email_matches(supplied: str, stored: str) -> bool:
    return _equal(supplied.strip().casefold(), stored.strip().casefold())


def _identity_matches(kind: str, supplied: str, order: OrderRecord | None) -> bool:
    if order is None:
        # Same work as a real comparison: avoid a timing oracle for order existence.
        _equal(supplied, _DUMMY)
        return False
    if kind == "EMAIL":
        return _email_matches(supplied, order.customer_email)
    return _phone_matches(supplied, order.customer_phone)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _actor(ctx: ToolContext) -> str:
    return ctx.actor_ref or str(ctx.conversation_id)


def order_failure_key(ctx: ToolContext, order_id: int) -> str:
    return f"orderfail:{ctx.tenant_id}:order:{_sha256(str(order_id))}"


def actor_failure_key(ctx: ToolContext) -> str:
    return f"orderfail:{ctx.tenant_id}:actor:{_actor(ctx)}"


async def _failure_counts(ctx: ToolContext, keys: tuple[str, str]) -> tuple[int, int]:
    try:
        return await ctx.limiter.failures(keys[0]), await ctx.limiter.failures(keys[1])
    except Exception:
        raise _LimiterUnavailableError from None


async def _record_failure(ctx: ToolContext, keys: tuple[str, str]) -> tuple[int, int]:
    window = get_settings().order_lookup_lockout_seconds
    try:
        return (
            await ctx.limiter.record_failure(keys[0], window),
            await ctx.limiter.record_failure(keys[1], window),
        )
    except Exception:
        raise _LimiterUnavailableError from None


async def _audit_failure(
    ctx: ToolContext, contact_kind: str, counts: tuple[int, int], locked: bool
) -> None:
    """Audit row without PII: no order id, no contact value, only an actor hash."""
    async with ctx.session_factory() as session:
        session.add(
            AuditEvent(
                tenant_id=ctx.tenant_id,
                event_type=AUDIT_EVENT_TYPE,
                actor="tool:lookup_order",
                resource_type="conversation",
                resource_id=str(ctx.conversation_id),
                correlation_id=ctx.correlation_id[:64],
                data={
                    "actor_hash": _sha256(_actor(ctx))[:32],
                    "contact_kind": contact_kind.lower(),
                    "order_failures": counts[0],
                    "actor_failures": counts[1],
                    "locked": locked,
                },
            )
        )
        await session.commit()


async def lookup_order(
    ctx: ToolContext, order_token: str, contact_token: str
) -> ToolResult[OrderStatusView]:
    """Verify the contact against the order's customer and return a tokenized status."""
    args: dict[str, Any] = {
        "order_token": _safe_token(order_token),
        "contact_token": _safe_token(contact_token),
    }
    raw: dict[str, Any] = {"order_token": order_token, "contact_token": contact_token}
    max_failures = get_settings().order_lookup_max_failures
    # Audit-only extras merged into this call's ``result_summary`` (review L-4).
    extra: dict[str, object] = {}

    async def op() -> ToolResult[OrderStatusView]:
        params = LookupOrderInput.model_validate(raw)
        order_hit = ctx.vault.resolve(params.order_token)
        if order_hit is None or order_hit[0] != "ORDER":
            return ToolResult.failure("invalid_input", MSG_BAD_ORDER_REF)
        try:
            order_id = int(order_hit[1].strip().lstrip("#"))
        except ValueError:
            return ToolResult.failure("invalid_input", MSG_BAD_ORDER_REF)
        if not 0 < order_id < 2**31:
            return ToolResult.failure("invalid_input", MSG_BAD_ORDER_REF)

        contact_hit = ctx.vault.resolve(params.contact_token)
        if contact_hit is None or contact_hit[0] not in ("EMAIL", "PHONE"):
            return ToolResult.failure("invalid_input", MSG_BAD_CONTACT)
        contact_kind, contact_value = contact_hit

        keys = (order_failure_key(ctx, order_id), actor_failure_key(ctx))
        try:
            counts = await _failure_counts(ctx, keys)
            if max(counts) >= max_failures:
                extra.update(failed_verifications=max(counts), locked=True)
                return ToolResult.failure("not_authorized", MSG_LOCKED)

            order = await ctx.commerce.get_order(order_id)
            verified = _identity_matches(contact_kind, contact_value, order)
            if order is None or not verified:
                counts = await _record_failure(ctx, keys)
        except _LimiterUnavailableError:
            extra["limiter_unavailable"] = True
            return ToolResult.failure("unavailable", MSG_UNAVAILABLE)

        if order is None or not verified:
            locked = max(counts) >= max_failures
            extra.update(failed_verifications=max(counts), locked=locked)
            await _audit_failure(ctx, contact_kind, counts, locked)
            return ToolResult.failure("not_authorized", MSG_NOT_AUTHORIZED)

        tracking_token = (
            ctx.vault.tokenize("TRACKING", order.tracking_code) if order.tracking_code else None
        )
        delivery_token = (
            ctx.vault.tokenize("DATE", order.estimated_delivery.isoformat())
            if order.estimated_delivery
            else None
        )
        normalized = order.status.strip().casefold()
        status = cast(OrderStatus, normalized) if normalized in _KNOWN_STATUSES else "unknown"
        return ToolResult.success(
            OrderStatusView(
                identity_verified=True,
                status=status,
                has_tracking=tracking_token is not None,
                tracking_token=tracking_token,
                estimated_delivery_token=delivery_token,
                order_token=params.order_token.strip(),
            )
        )

    result = await run_tool(
        ctx,
        "lookup_order",
        args,
        op,
        lambda data: {"identity_verified": data.identity_verified},
    )
    # run_tool appended this call's record with no await in between: [-1] is ours.
    if extra:
        ctx.tool_calls[-1].result_summary.update(extra)
    return result


__all__ = [
    "AUDIT_EVENT_TYPE",
    "MSG_LOCKED",
    "MSG_NOT_AUTHORIZED",
    "OrderStatusView",
    "actor_failure_key",
    "lookup_order",
    "order_failure_key",
]
