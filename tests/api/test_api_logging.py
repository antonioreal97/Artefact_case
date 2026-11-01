"""Logs are JSON, carry request context, and never contain PII, bodies or API keys."""

from __future__ import annotations

import io
import json

import pytest

from app.core.logging import bind_correlation_id, clear_context, configure_logging, get_logger

pytestmark = pytest.mark.integration

EMAIL = "maria.silva@example.com"
PHONE = "(11) 98765-4321"
MESSAGE = f"Meu email é {EMAIL} e meu telefone {PHONE}, pedido 4521"


@pytest.fixture
def log_stream(app, api_settings):
    stream = io.StringIO()
    configure_logging(api_settings, stream=stream)
    yield stream
    configure_logging(api_settings)


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


async def test_request_logs_have_no_pii_or_key(client, two_tenants, make_key, log_stream, fakes):
    key = await make_key(two_tenants[0])
    r = await client.post(
        "/v1/chat/messages",
        headers={"X-API-Key": key, "X-Request-ID": "b000000000000001"},
        json={"message": MESSAGE},
    )
    assert r.status_code == 200
    # also a failed auth attempt with a full (bogus) key
    await client.get("/v1/handoffs", headers={"X-API-Key": key[:-3] + "zzz"})
    fakes.chat.error = RuntimeError(f"crash for {EMAIL}")
    await client.post("/v1/chat/messages", headers={"X-API-Key": key}, json={"message": MESSAGE})

    output = log_stream.getvalue()
    lines = _lines(log_stream)
    assert lines, "expected JSON log lines"
    for needle in (EMAIL, "98765", "pedido 4521", key, key.split("_", 2)[2], "Meu email"):
        assert needle not in output, needle
    completed = [ln for ln in lines if ln["event"] == "request.completed"]
    first = next(ln for ln in completed if ln.get("correlation_id") == "b000000000000001")
    assert first["route"] == "/v1/chat/messages" and first["status_code"] == 200
    assert first["tenant_id"] == str(two_tenants[0])
    turn = next(ln for ln in lines if ln["event"] == "chat.turn_completed")
    assert turn["conversation_id"] == r.json()["conversation_id"]
    crash = next(ln for ln in lines if ln["event"] == "request.unhandled_error")
    assert crash["exc_type"] == "RuntimeError" and "stack" in crash
    auth_failed = next(ln for ln in lines if ln["event"] == "auth.failed")
    assert auth_failed["key_prefix"] == key.split("_")[1]


def test_redaction_processor_unit():
    stream = io.StringIO()
    from app.core.config import Settings

    configure_logging(Settings(app_env="test"), stream=stream)
    try:
        bind_correlation_id("unit-1")
        get_logger("t").info(
            "something happened",
            email=EMAIL,
            note=f"ligar para {PHONE}",
            text="conteúdo do cliente",
            prompt="system prompt",
            api_key="emp_abcdefgh_secret",
            nested={"phone": "11987654321", "ok": 1},
        )
    finally:
        clear_context()
        configure_logging(Settings(app_env="test"))
    line = json.loads(stream.getvalue())
    assert line["correlation_id"] == "unit-1"
    assert line["email"] == "[REDACTED]"
    assert "98765" not in line["note"]
    assert line["text"] == line["prompt"] == line["api_key"] == "[OMITTED]"
    assert line["nested"] == {"phone": "[REDACTED]", "ok": 1}
