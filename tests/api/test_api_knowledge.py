"""Knowledge upload: size limits (413), type checks (415), receipts and reprocessing."""

from __future__ import annotations

import io
import zipfile

import pytest
from sqlalchemy import update

from app.api import dependencies as deps
from app.models import DocumentVersion

pytestmark = pytest.mark.integration

UPLOAD = "/v1/knowledge/documents"


def _docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


@pytest.fixture
async def headers(two_tenants, make_key):
    return {"X-API-Key": await make_key(two_tenants[0])}


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("faq.txt", "Horário: 9h às 18h".encode()),
        ("faq.md", b"# FAQ\n\nTrocas em 7 dias"),
        ("politicas.pdf", b"%PDF-1.7\n..."),
        ("manual.docx", _docx_bytes()),
    ],
)
async def test_upload_accepted(client, headers, fakes, two_tenants, name, data):
    r = await client.post(
        UPLOAD,
        headers=headers,
        data={"title": "Políticas", "category": "politicas"},
        files={"file": (name, data, "application/octet-stream")},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending" and body["version"] == 1 and body["duplicate"] is False
    assert fakes.enqueuer.calls[-1] == (two_tenants[0], __import__("uuid").UUID(body["version_id"]))


async def test_upload_too_large_413(client, headers, api_settings, fakes):
    too_big = b"a" * (api_settings.max_upload_bytes + 1)
    r = await client.post(
        UPLOAD, headers=headers, data={"title": "x"}, files={"file": ("big.txt", too_big)}
    )
    assert r.status_code == 413
    assert r.json()["error"] == "payload_too_large"
    assert fakes.enqueuer.calls == []


async def test_upload_body_far_too_large_rejected_by_middleware(client, headers, api_settings):
    huge = b"a" * (api_settings.max_upload_bytes + 200_000)
    r = await client.post(
        UPLOAD, headers=headers, data={"title": "x"}, files={"file": ("big.txt", huge)}
    )
    assert r.status_code == 413
    assert "correlation_id" in r.json()


async def test_streamed_body_without_length_is_capped(client, headers, api_settings):
    async def gen():
        for _ in range(100):
            yield b"x" * 20_000

    r = await client.post(
        "/v1/knowledge/documents/text",
        headers={**headers, "Content-Type": "application/json"},
        content=gen(),
    )
    assert r.status_code == 413


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("malware.exe", b"MZ\x90\x00"),
        ("noext", b"hello"),
        ("fake.pdf", b"just text pretending to be a pdf"),
        ("fake.docx", b"PK\x03\x04not really a zip"),
        ("zip.docx", b""),  # replaced below by a zip without word/document.xml
        ("binary.txt", b"\x00\x01\x02\xff"),
    ],
)
async def test_bad_type_or_fake_extension_415(client, headers, name, data):
    if name == "zip.docx":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("evil.sh", "rm -rf /")
        data = buf.getvalue()
    r = await client.post(
        UPLOAD, headers=headers, data={"title": "x"}, files={"file": (name, data)}
    )
    assert r.status_code == 415, r.text
    assert r.json()["error"] == "unsupported_media_type"


async def test_empty_file_422(client, headers):
    r = await client.post(
        UPLOAD, headers=headers, data={"title": "x"}, files={"file": ("a.txt", b"")}
    )
    assert r.status_code == 422


async def test_text_document_and_version_lifecycle(client, headers, fakes, session_factory):
    r = await client.post(
        "/v1/knowledge/documents/text",
        headers=headers,
        json={"title": "Trocas", "content": "Trocas em até 7 dias.", "category": "politicas"},
    )
    assert r.status_code == 202
    receipt = r.json()
    doc_id, version_id = receipt["document_id"], receipt["version_id"]

    v2 = await client.post(
        f"{UPLOAD}/{doc_id}/versions",
        headers=headers,
        files={"file": ("trocas.txt", "Trocas em até 30 dias.".encode())},
    )
    assert v2.status_code == 202 and v2.json()["version"] == 2

    detail = (await client.get(f"{UPLOAD}/{doc_id}", headers=headers)).json()
    assert detail["title"] == "Trocas" and [v["version"] for v in detail["versions"]] == [2, 1]
    listing = (await client.get(UPLOAD, headers=headers, params={"category": "politicas"})).json()
    assert [d["id"] for d in listing] == [doc_id]

    async with session_factory() as s:
        await s.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == version_id)
            .values(ingestion_status="failed", error_message="Falha ao extrair texto.")
        )
        await s.commit()
    status = (await client.get(f"{UPLOAD}/{doc_id}/versions/{version_id}", headers=headers)).json()
    assert status["status"] == "failed" and status["error_message"] == "Falha ao extrair texto."

    calls_before = len(fakes.enqueuer.calls)
    again = await client.post(f"{UPLOAD}/{doc_id}/versions/{version_id}/reprocess", headers=headers)
    assert again.status_code == 202 and again.json()["status"] == "pending"
    assert len(fakes.enqueuer.calls) == calls_before + 1
    status = (await client.get(f"{UPLOAD}/{doc_id}/versions/{version_id}", headers=headers)).json()
    assert status["status"] == "pending" and status["error_message"] is None


async def test_text_document_too_long_413(client, headers, api_settings):
    r = await client.post(
        "/v1/knowledge/documents/text",
        headers=headers,
        json={"title": "t", "content": "a" * (api_settings.max_text_chars + 1)},
    )
    assert r.status_code == 413


async def test_real_ingestion_creator_and_enqueue_failure_tolerated(app, client, headers, fakes):
    """Uses Task C's real create_document_version; a failing enqueue still yields 202."""
    pytest.importorskip("app.rag.ingestion")
    app.dependency_overrides.pop(deps.get_version_creator)

    async def broken_enqueue(tenant_id, version_id):
        raise ConnectionError("redis down")

    app.dependency_overrides[deps.get_ingestion_enqueuer] = lambda: broken_enqueue
    r = await client.post(
        "/v1/knowledge/documents/text",
        headers=headers,
        json={"title": "Horários", "content": "A loja abre de segunda a sábado, das 9h às 18h."},
    )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending"
