"""Order status tool (RESTRICTED data, trusted local boundary).

The model only ever handles vault tokens (``[[ORDER_1]]``, ``[[EMAIL_1]]``/``[[PHONE_1]]``).
Tokens are resolved locally, identity is verified deterministically and the model gets an
abstract ``OrderStatusView`` whose restricted details are new vault tokens
(``[[TRACKING_n]]``, ``[[DATE_n]]``) that trusted code reveals in the final answer.

No enumeration: a nonexistent order and an identity mismatch return the SAME failure.

Brute force: failed verifications are counted in ``ctx.vault.counters["_ORDER_FAILS"]``.
The vault is per conversation and persisted with it (TTL), so the counter spans turns of
the same conversation. After ``MAX_FAILED_VERIFICATIONS`` failures every further lookup is
refused with ``not_authorized`` and a handoff suggestion. The counter is never reset by a
later success (that would let an attacker interleave one known order with guesses).
"""

from __future__ import annotations

import hmac
import re
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from app.integrations.base import OrderRecord
from app.schemas.tools import ToolResult
from app.services.privacy_vault import TOKEN_RE
from app.tools.catalog import run_tool
from app.tools.context import ToolContext

OrderStatus = Literal["pending", "confirmed", "shipped", "delivered", "cancelled", "unknown"]
_KNOWN_STATUSES: frozenset[str] = frozenset(
    {"pending", "confirmed", "shipped", "delivered", "cancelled"}
)

FAILS_COUNTER_KEY = "_ORDER_FAILS"
MAX_FAILED_VERIFICATIONS = 3

MSG_NOT_AUTHORIZED = (
    "Não foi possível confirmar os dados do pedido. Verifique o número do pedido e o "
    "e-mail ou telefone usado na compra."
)
MSG_LOCKED = (
    "Por segurança, não posso mais consultar pedidos nesta conversa após várias tentativas "
    "sem confirmação. Posso encaminhar você para um atendente humano."
)
MSG_BAD_ORDER_REF = "Não consegui identificar o número do pedido. Pode informá-lo novamente?"
MSG_BAD_CONTACT = (
    "Para consultar o pedido preciso do e-mail ou telefone usado na compra. Pode informá-lo?"
)

_DUMMY = "\x00dummy-contact-for-constant-time\x00"


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
    """Compare digits only. Tolerates +55, missing area code and the legacy 8-digit mobile
    form by comparing the last 8 digits, plus the area code when both sides carry one."""
    s, t = _national_digits(supplied), _national_digits(stored)
    if len(s) < 8 or len(t) < 8:
        _equal(_DUMMY, _DUMMY)
        return False
    ok = _equal(s[-8:], t[-8:])
    if len(s) >= 10 and len(t) >= 10:
        ok = _equal(s[:2], t[:2]) and ok
    return ok


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


async def lookup_order(
    ctx: ToolContext, order_token: str, contact_token: str
) -> ToolResult[OrderStatusView]:
    """Verify the contact against the order's customer and return a tokenized status."""
    args: dict[str, Any] = {
        "order_token": _safe_token(order_token),
        "contact_token": _safe_token(contact_token),
    }
    raw: dict[str, Any] = {"order_token": order_token, "contact_token": contact_token}

    async def op() -> ToolResult[OrderStatusView]:
        params = LookupOrderInput.model_validate(raw)
        if ctx.vault.counters.get(FAILS_COUNTER_KEY, 0) >= MAX_FAILED_VERIFICATIONS:
            return ToolResult.failure("not_authorized", MSG_LOCKED)

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

        order = await ctx.commerce.get_order(order_id)
        verified = _identity_matches(contact_kind, contact_value, order)
        if order is None or not verified:
            ctx.vault.counters[FAILS_COUNTER_KEY] = ctx.vault.counters.get(FAILS_COUNTER_KEY, 0) + 1
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
    if not result.ok:
        ctx.tool_calls[-1].result_summary["failed_verifications"] = ctx.vault.counters.get(
            FAILS_COUNTER_KEY, 0
        )
    return result


__all__ = [
    "FAILS_COUNTER_KEY",
    "MAX_FAILED_VERIFICATIONS",
    "OrderStatusView",
    "lookup_order",
]
