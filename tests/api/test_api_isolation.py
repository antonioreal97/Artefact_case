"""Tenant isolation: tenant B's key can never read tenant A's resources (404, not 403),
and a tenant_id smuggled in a body is ignored."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Conversation

pytestmark = pytest.mark.integration


async def _seed_tenant_a(client, key_a):
    h = {"X-API-Key": key_a}
    chat = await client.post("/v1/chat/messages", headers=h, json={"message": "Prazo de troca?"})
    assert chat.status_code == 200, chat.text
    reply = chat.json()
    doc = await client.post(
        "/v1/knowledge/documents/text", headers=h, json={"title": "Políticas", "content": "Trocas."}
    )
    assert doc.status_code == 202, doc.text
    handoff = await client.post(
        "/v1/handoffs",
        headers=h,
        json={"conversation_id": reply["conversation_id"], "reason": "customer_request"},
    )
    assert handoff.status_code == 201, handoff.text
    return reply, doc.json(), handoff.json()


async def test_tenant_b_cannot_read_tenant_a_resources(client, two_tenants, make_key):
    tenant_a, tenant_b = two_tenants
    key_a, key_b = await make_key(tenant_a), await make_key(tenant_b)
    reply, receipt, handoff = await _seed_tenant_a(client, key_a)
    doc_id, version_id = receipt["document_id"], receipt["version_id"]
    paths = [
        f"/v1/conversations/{reply['conversation_id']}",
        f"/v1/conversations/{reply['conversation_id']}/messages",
        f"/v1/messages/{reply['message_id']}/sources",
        f"/v1/knowledge/documents/{doc_id}",
        f"/v1/knowledge/documents/{doc_id}/versions/{version_id}",
        f"/v1/handoffs/{handoff['id']}",
    ]
    for path in paths:
        own = await client.get(path, headers={"X-API-Key": key_a})
        assert own.status_code == 200, (path, own.text)
        foreign = await client.get(path, headers={"X-API-Key": key_b})
        assert foreign.status_code == 404, (path, foreign.text)
        assert foreign.json()["error"] == "not_found"

    # writes on foreign ids are 404 as well
    h_b = {"X-API-Key": key_b}
    assert (
        await client.patch(
            f"/v1/handoffs/{handoff['id']}", headers=h_b, json={"status": "resolved"}
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/v1/knowledge/documents/{doc_id}/versions/{version_id}/reprocess", headers=h_b
        )
    ).status_code == 404
    new_version = await client.post(
        f"/v1/knowledge/documents/{doc_id}/versions",
        headers=h_b,
        files={"file": ("v2.txt", b"nova", "text/plain")},
    )
    assert new_version.status_code == 404
    assert (
        await client.post(
            "/v1/handoffs",
            headers=h_b,
            json={"conversation_id": reply["conversation_id"], "reason": "customer_request"},
        )
    ).status_code == 404

    # list endpoints only show own data
    docs_b = (await client.get("/v1/knowledge/documents", headers=h_b)).json()
    assert all(d["id"] != doc_id for d in docs_b)
    handoffs_b = (await client.get("/v1/handoffs", headers=h_b)).json()
    assert all(h["id"] != handoff["id"] for h in handoffs_b)

    own_messages = (await client.get(paths[1], headers={"X-API-Key": key_a})).json()
    assert [m["role"] for m in own_messages] == ["user", "assistant"]
    sources = (await client.get(paths[2], headers={"X-API-Key": key_a})).json()
    assert sources[0]["label"] == "S1"


async def test_chat_foreign_conversation_404(client, two_tenants, make_key, fakes):
    key_a, key_b = await make_key(two_tenants[0]), await make_key(two_tenants[1])
    reply, _, _ = await _seed_tenant_a(client, key_a)
    r = await client.post(
        "/v1/chat/messages",
        headers={"X-API-Key": key_b},
        json={"conversation_id": reply["conversation_id"], "message": "oi"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Conversation not found"
    assert fakes.chat.calls[-1]["tenant_id"] == two_tenants[1]


async def test_tenant_id_in_body_or_query_is_ignored(
    client, two_tenants, make_key, fakes, session_factory
):
    tenant_a, tenant_b = two_tenants
    key_b = await make_key(tenant_b)
    r = await client.post(
        f"/v1/chat/messages?tenant_id={tenant_a}",
        headers={"X-API-Key": key_b},
        json={"message": f"sou do tenant {tenant_a}", "tenant_id": str(tenant_a)},
    )
    assert r.status_code == 200
    assert fakes.chat.calls[-1]["tenant_id"] == tenant_b
    async with session_factory() as s:
        conv = await s.scalar(
            select(Conversation).where(Conversation.id == r.json()["conversation_id"])
        )
        assert conv is not None and conv.tenant_id == tenant_b

    doc = await client.post(
        "/v1/knowledge/documents/text",
        headers={"X-API-Key": key_b},
        json={"title": "x", "content": "y", "tenant_id": str(tenant_a)},
    )
    assert doc.status_code == 202
    assert fakes.enqueuer.calls[-1][0] == tenant_b
