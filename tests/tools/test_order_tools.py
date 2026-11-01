from __future__ import annotations

import dataclasses
import json

import pytest

from app.tools.orders import (
    MSG_LOCKED,
    OrderStatusView,
    actor_failure_key,
    lookup_order,
)
from emporio_agent.privacy import (
    DataClassification,
    assert_external_payload_safe,
    find_restricted_data,
)

A_EMAIL = "Lucas.Mendes@jmail.com"
A_PHONE = "(67) 99812-3456"
A_NAME = "Lucas Mendes da Silva"
A_TRACKING = "BRAB1234567BR"
B_EMAIL = "cliente@lojasintetica.test"
RAW_VALUES = (A_EMAIL.casefold(), "99812", "3456", A_NAME, A_TRACKING, "2026-11-05", "101")


def assert_no_raw(payload: object) -> None:
    dumped = json.dumps(payload, default=str).casefold()
    for raw in RAW_VALUES:
        assert raw.casefold() not in dumped, raw


def test_order_status_view_passes_external_privacy_guard():
    view = OrderStatusView(
        identity_verified=True,
        status="shipped",
        has_tracking=True,
        tracking_token="[[TRACKING_1]]",
        estimated_delivery_token="[[DATE_1]]",
        order_token="[[ORDER_1]]",
    )
    assert_external_payload_safe(
        view.model_dump(mode="json"), classification=DataClassification.INTERNAL
    )


integration = pytest.mark.integration


@integration
async def test_lookup_success_returns_only_tokens(ctx_a):
    order = ctx_a.vault.tokenize("ORDER", "101")
    email = ctx_a.vault.tokenize("EMAIL", "  lucas.MENDES@jmail.com ")  # casefold + strip
    res = await lookup_order(ctx_a, order, email)
    assert res.ok, res.error
    view = res.data
    assert view.identity_verified and view.status == "shipped" and view.has_tracking
    assert view.order_token == order
    assert ctx_a.vault.resolve(view.tracking_token) == ("TRACKING", A_TRACKING)
    assert ctx_a.vault.resolve(view.estimated_delivery_token) == ("DATE", "2026-11-05")

    payload = view.model_dump(mode="json")
    assert_external_payload_safe(payload, classification=DataClassification.INTERNAL)
    assert_no_raw(payload)
    assert ctx_a.vault.reveal(view.tracking_token) == A_TRACKING  # trusted reveal works


@integration
async def test_lookup_without_tracking(ctx_a):
    res = await lookup_order(
        ctx_a, ctx_a.vault.tokenize("ORDER", "102"), ctx_a.vault.tokenize("PHONE", A_PHONE)
    )
    assert res.ok
    assert res.data.status == "pending"
    assert res.data.has_tracking is False
    assert res.data.tracking_token is None and res.data.estimated_delivery_token is None


@integration
@pytest.mark.parametrize(
    "phone",
    [
        "(67) 99812-3456",
        "+55 67 99812-3456",
        "5567998123456",
        "067 99812 3456",
        "(67) 9812-3456",  # legacy 8-digit mobile form, same area code
    ],
)
async def test_phone_variants_verify(ctx_a, phone):
    res = await lookup_order(
        ctx_a, ctx_a.vault.tokenize("ORDER", "101"), ctx_a.vault.tokenize("PHONE", phone)
    )
    assert res.ok, phone


@integration
@pytest.mark.parametrize(
    "phone",
    [
        "(11) 99812-3456",
        "(67) 99812-0000",
        "1234",
        # Review L-3: subscriber digits without the area code are no longer accepted.
        "99812-3456",
        "9812-3456",
        "+55 9 9812 3456",
        "(67) 8812-3456",  # 8-digit form only matches when the 9-digit number starts with 9
    ],
)
async def test_phone_mismatch_fails(ctx_a, phone):
    res = await lookup_order(
        ctx_a, ctx_a.vault.tokenize("ORDER", "101"), ctx_a.vault.tokenize("PHONE", phone)
    )
    assert not res.ok and res.error.code == "not_authorized"


@integration
async def test_no_enumeration_mismatch_equals_missing_order(ctx_a):
    wrong_contact = await lookup_order(
        ctx_a, ctx_a.vault.tokenize("ORDER", "101"), ctx_a.vault.tokenize("EMAIL", "x@y.com")
    )
    missing_order = await lookup_order(
        ctx_a, ctx_a.vault.tokenize("ORDER", "99999"), ctx_a.vault.tokenize("EMAIL", A_EMAIL)
    )
    assert not wrong_contact.ok and not missing_order.ok
    assert wrong_contact.model_dump() == missing_order.model_dump()
    assert wrong_contact.error.code == "not_authorized"
    s1, s2 = (r.result_summary for r in ctx_a.tool_calls)
    assert {k for k in s1 if k != "failed_verifications"} == {
        k for k in s2 if k != "failed_verifications"
    }


@integration
async def test_brute_force_lock_after_three_failures(ctx_a):
    order = ctx_a.vault.tokenize("ORDER", "101")
    for i in range(3):
        bad = ctx_a.vault.tokenize("EMAIL", f"guess{i}@x.com")
        res = await lookup_order(ctx_a, order, bad)
        assert res.error.code == "not_authorized"
    assert await ctx_a.limiter.failures(actor_failure_key(ctx_a)) == 3
    # Correct contact is refused now; message suggests a human handoff.
    locked = await lookup_order(ctx_a, order, ctx_a.vault.tokenize("EMAIL", A_EMAIL))
    assert not locked.ok and locked.error.code == "not_authorized"
    assert locked.error.message == MSG_LOCKED and "atendente" in locked.error.message
    assert ctx_a.tool_calls[-1].result_summary["locked"] is True
    assert ctx_a.tool_calls[-2].result_summary["locked"] is True  # 3rd failure hit the limit
    assert ctx_a.tool_calls[0].result_summary["locked"] is False


@integration
async def test_invalid_tokens(ctx_a):
    email = ctx_a.vault.tokenize("EMAIL", A_EMAIL)
    cases = [
        ("101", email),  # raw value instead of a token
        ("[[ORDER_9]]", email),  # unknown token
        (ctx_a.vault.tokenize("ORDER", "abc"), email),  # does not parse to int
        (ctx_a.vault.tokenize("ORDER", "101"), ctx_a.vault.tokenize("NAME", A_NAME)),
        (email, email),  # wrong kind for order
    ]
    for order_token, contact_token in cases:
        res = await lookup_order(ctx_a, order_token, contact_token)
        assert not res.ok and res.error.code == "invalid_input", (order_token, contact_token)
    assert await ctx_a.limiter.failures(actor_failure_key(ctx_a)) == 0  # not identity failures
    assert ctx_a.tool_calls[0].arguments_redacted["order_token"] == "[NOT_A_TOKEN]"


@integration
async def test_cross_tenant_order_isolation(ctx_a, ctx_b):
    # Tenant B cannot read A's orders even with the right contact (order 102 is only in A).
    res = await lookup_order(
        ctx_b, ctx_b.vault.tokenize("ORDER", "102"), ctx_b.vault.tokenize("EMAIL", A_EMAIL)
    )
    assert res.error.code == "not_authorized"
    # Order 101 exists in both tenants: each verifies only against its own customer.
    res = await lookup_order(
        ctx_b, ctx_b.vault.tokenize("ORDER", "101"), ctx_b.vault.tokenize("EMAIL", A_EMAIL)
    )
    assert res.error.code == "not_authorized"
    res = await lookup_order(
        ctx_a, ctx_a.vault.tokenize("ORDER", "101"), ctx_a.vault.tokenize("EMAIL", B_EMAIL)
    )
    assert res.error.code == "not_authorized"
    ok_b = await lookup_order(
        ctx_b, ctx_b.vault.tokenize("ORDER", "101"), ctx_b.vault.tokenize("EMAIL", B_EMAIL)
    )
    assert ok_b.ok and ok_b.data.status == "delivered"


@integration
async def test_tool_call_records_contain_no_pii(ctx_a):
    order = ctx_a.vault.tokenize("ORDER", "101")
    await lookup_order(ctx_a, order, ctx_a.vault.tokenize("EMAIL", A_EMAIL))
    await lookup_order(ctx_a, order, ctx_a.vault.tokenize("PHONE", "(11) 90000-0000"))
    await lookup_order(ctx_a, "pedido 101", A_EMAIL)  # raw values passed by mistake
    records = [dataclasses.asdict(r) for r in ctx_a.tool_calls]
    assert [r["status"] for r in records] == ["success", "error", "error"]
    assert records[0]["result_summary"] == {"identity_verified": True}
    assert records[1]["result_summary"] == {
        "error_code": "not_authorized",
        "failed_verifications": 1,
        "locked": False,
    }
    assert find_restricted_data(records) == ()
    assert_no_raw(records)
    assert "(11) 90000-0000" not in json.dumps(records)
