"""Attack 3: retrieval leakage between tenants (identical/similar docs, embedding cache)."""

from __future__ import annotations

import pytest
from adv_support import SHARED_DOC, World, chat, leaked

pytestmark = pytest.mark.integration


async def _sources(client, tenant, message_id: str) -> list[dict]:
    response = await client.get(f"/v1/messages/{message_id}/sources", headers=tenant.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_identical_documents_only_cite_own_tenant(client, world: World) -> None:
    question = "Qual o horário de funcionamento da loja aos domingos?"
    for tenant, other in ((world.a, world.b), (world.b, world.a)):
        reply = await chat(client, tenant, question)
        assert reply["sources"], reply  # the identical doc must be found for both tenants
        cited = {s["document_id"] for s in reply["sources"]}
        assert cited <= {str(tenant.shared_doc_id), str(tenant.private_doc_id)}, cited
        assert str(other.shared_doc_id) not in cited
        stored = await _sources(client, tenant, reply["message_id"])
        assert {s["document_id"] for s in stored} <= {
            str(tenant.shared_doc_id),
            str(tenant.private_doc_id),
        }
        assert {s["document_version_id"] for s in stored} <= {str(tenant.shared_version_id)}


@pytest.mark.parametrize("warm_cache_with_a", [False, True])
async def test_private_document_never_reaches_other_tenant(
    client, world: World, model_control, warm_cache_with_a: bool
) -> None:
    """B asks exactly A's private wording; the shared (tenant-agnostic) query-embedding cache
    may already hold this query from A. B must only ever see its own document."""
    question = "Qual é o código interno da garantia estendida que cobre cordas e trastes?"
    if warm_cache_with_a:
        control = await chat(client, world.a, question)
        assert world.a.data.marker in control["reply"], control  # sanity: A sees its own doc
    before = len(model_control.calls)
    reply = await chat(client, world.b, question)
    a_doc = str(world.a.private_doc_id)
    assert world.a.data.marker not in reply["reply"]
    assert "vinte e quatro meses" not in reply["reply"]
    assert a_doc not in {s["document_id"] for s in reply["sources"]}
    # Not even the model may see tenant A's text while serving tenant B.
    b_payloads = model_control.payloads_since(before)
    # (the question itself contains "cordas e trastes"; A's private wording does not leak)
    assert not leaked(b_payloads, [world.a.data.marker, "vinte e quatro meses", a_doc])
    if reply["message_id"]:
        stored = await _sources(client, world.b, reply["message_id"])
        assert all(world.a.data.marker not in s["quote"] for s in stored)
        assert a_doc not in {s["document_id"] for s in stored}


async def test_catalog_text_of_other_tenant_not_retrievable(client, world: World) -> None:
    reply = await chat(client, world.b, f"O que é o produto exclusivo {world.a.data.marker}?")
    assert world.a.data.marker not in reply["reply"]
    assert world.a.data.product_name not in reply["reply"]


async def test_document_listing_and_version_reads_are_tenant_scoped(client, world: World):
    listing = await client.get("/v1/knowledge/documents?limit=200", headers=world.b.headers)
    assert {d["id"] for d in listing.json()} == {
        str(world.b.private_doc_id),
        str(world.b.shared_doc_id),
    }
    assert all(SHARED_DOC not in str(d) for d in listing.json())  # no content in listings
