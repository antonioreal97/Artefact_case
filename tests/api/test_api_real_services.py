"""API wired to Task E's real ChatService (deterministic fake LLM, in-memory vault)."""

from __future__ import annotations

import pytest

from app.api import dependencies as deps
from app.core.metrics import HANDOFFS

pytestmark = pytest.mark.integration


async def test_real_chat_service_end_to_end(
    app, client, two_tenants, make_key, api_settings, session_factory
):
    chat_module = pytest.importorskip("app.services.chat")
    vault_module = pytest.importorskip("app.services.vault_store")
    service = chat_module.ChatService(
        session_factory, api_settings, vault_module.InMemoryVaultStore()
    )
    app.dependency_overrides[deps.get_chat_service] = lambda: service
    key_a, key_b = await make_key(two_tenants[0]), await make_key(two_tenants[1])

    before = HANDOFFS.value(reason="no_information", origin="agent")
    r = await client.post(
        "/v1/chat/messages",
        headers={"X-API-Key": key_a},
        json={"message": "Qual o horário de funcionamento da loja?"},
    )
    assert r.status_code == 200, r.text
    reply = r.json()
    assert reply["reply"]
    if reply["handoff"] and reply["handoff"]["reason"] == "no_information":
        assert HANDOFFS.value(reason="no_information", origin="agent") == before + 1

    history = await client.get(
        f"/v1/conversations/{reply['conversation_id']}/messages", headers={"X-API-Key": key_a}
    )
    assert [m["role"] for m in history.json()] == ["user", "assistant"]

    foreign = await client.post(
        "/v1/chat/messages",
        headers={"X-API-Key": key_b},
        json={"conversation_id": reply["conversation_id"], "message": "oi"},
    )
    assert foreign.status_code == 404
