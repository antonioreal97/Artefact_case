"""Correlation ids, error envelopes, validation without echo, body limits, health, metrics."""

from __future__ import annotations

import pytest

from app.core.metrics import HANDOFFS, MODEL_ERRORS

pytestmark = pytest.mark.integration


@pytest.fixture
async def headers(two_tenants, make_key):
    return {"X-API-Key": await make_key(two_tenants[0])}


async def test_request_id_propagated_and_generated(client):
    rid = "0123456789abcdefABCDEF0123456789"
    r = await client.get("/health", headers={"X-Request-ID": rid})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["X-Request-ID"] == rid

    generated = await client.get("/health")
    assert len(generated.headers["X-Request-ID"]) == 32

    bad = await client.get("/health", headers={"X-Request-ID": "x" * 65})
    assert bad.headers["X-Request-ID"] != "x" * 65
    injected = await client.get("/health", headers={"X-Request-ID": "a b<script>"})
    assert injected.headers["X-Request-ID"] != "a b<script>"


async def test_500_hides_internals_and_returns_correlation_id(client, headers, fakes):
    fakes.chat.error = RuntimeError("db password=hunter2 for cliente@example.com")
    before = MODEL_ERRORS.value(error_type="RuntimeError")
    r = await client.post(
        "/v1/chat/messages",
        headers={**headers, "X-Request-ID": "c000000000000001"},
        json={"message": "oi"},
    )
    assert r.status_code == 500
    assert r.json() == {"error": "internal_error", "correlation_id": "c000000000000001"}
    assert r.headers["X-Request-ID"] == "c000000000000001"
    assert "hunter2" not in r.text and "Traceback" not in r.text
    assert MODEL_ERRORS.value(error_type="RuntimeError") == before + 1


async def test_validation_422_does_not_echo_input(client, headers):
    secret = "MEU-SEGREDO-cliente@example.com "
    r = await client.post("/v1/chat/messages", headers=headers, json={"message": secret * 200})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation_error"
    assert body["detail"][0]["loc"] == ["body", "message"]
    assert "MEU-SEGREDO" not in r.text and "example.com" not in r.text

    empty = await client.post("/v1/chat/messages", headers=headers, json={"message": ""})
    assert empty.status_code == 422
    bad_json = await client.post(
        "/v1/chat/messages",
        headers={**headers, "Content-Type": "application/json"},
        content=b'{"message": "MEU-SEGREDO',
    )
    assert bad_json.status_code == 422 and "MEU-SEGREDO" not in bad_json.text


async def test_json_body_limit_413(client, headers):
    r = await client.post(
        "/v1/handoffs",
        headers={**headers, "Content-Type": "application/json"},
        content=b'{"summary": "' + b"a" * (1024 * 1024) + b'"}',
    )
    assert r.status_code == 413
    assert r.json()["error"] == "payload_too_large"


async def test_unknown_route_404_envelope(client):
    r = await client.get("/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found" and r.json()["correlation_id"]


async def test_readiness(client, fakes):
    r = await client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}
    fakes.redis.fail = True
    r = await client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "checks": {"database": "ok", "redis": "fail"}}
    assert "redis down" not in r.text


async def test_chat_handoff_and_metrics_exposition(client, headers, fakes, two_tenants):
    fakes.chat.handoff_reason = "no_information"
    before = HANDOFFS.value(reason="no_information", origin="agent")
    r = await client.post("/v1/chat/messages", headers=headers, json={"message": "?"})
    assert r.status_code == 200
    assert HANDOFFS.value(reason="no_information", origin="agent") == before + 1

    m = await client.get("/metrics")
    assert m.status_code == 200
    assert m.headers["content-type"].startswith("text/plain")
    text = m.text
    assert "# TYPE http_requests_total counter" in text
    assert 'http_requests_total{method="POST",route="/v1/chat/messages",status="200"}' in text
    assert (
        'http_request_duration_seconds_bucket{method="POST",route="/v1/chat/messages",le="+Inf"}'
        in text
    )
    assert 'chat_turns_total{channel="api"}' in text
    assert 'retrieval_results_total{result="insufficient"}' in text
    # tenant-free: no tenant ids, no raw ids in route labels
    assert str(two_tenants[0]) not in text
    assert str(r.json()["conversation_id"]) not in text


async def test_handoff_crud_and_redacted_summary(client, headers):
    chat = (await client.post("/v1/chat/messages", headers=headers, json={"message": "oi"})).json()
    r = await client.post(
        "/v1/handoffs",
        headers=headers,
        json={
            "conversation_id": chat["conversation_id"],
            "reason": "sensitive_topic",
            "summary": "Cliente cliente@example.com ligou de (11) 98765-4321",
        },
    )
    assert r.status_code == 201
    handoff = r.json()
    assert "example.com" not in handoff["summary"] and "98765" not in handoff["summary"]
    assert handoff["status"] == "open"
    conv = (
        await client.get(f"/v1/conversations/{chat['conversation_id']}", headers=headers)
    ).json()
    assert conv["status"] == "handoff"

    upd = await client.patch(
        f"/v1/handoffs/{handoff['id']}", headers=headers, json={"status": "in_progress"}
    )
    assert upd.status_code == 200 and upd.json()["status"] == "in_progress"
    bad = await client.patch(f"/v1/handoffs/{handoff['id']}", headers=headers, json={"status": "x"})
    assert bad.status_code == 422
    listed = (
        await client.get("/v1/handoffs", headers=headers, params={"status": "in_progress"})
    ).json()
    assert [h["id"] for h in listed] == [handoff["id"]]
    assert (
        await client.get("/v1/handoffs", headers=headers, params={"status": "open"})
    ).json() == []
    bad_reason = await client.post(
        "/v1/handoffs",
        headers=headers,
        json={"conversation_id": chat["conversation_id"], "reason": "because"},
    )
    assert bad_reason.status_code == 422
