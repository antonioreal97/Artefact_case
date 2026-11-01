from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.core.config import Settings
from app.rag.chunking import chunk_blocks, tokenizer
from app.rag.extraction import TextBlock, extract_document

POLICY = Path("Desafio Técnico AI Engineer 2026/data/políticas_da_loja.pdf")


def pdf_bytes(*, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    for text in ("Returns policy", "Warranty policy"):
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 250 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = stream
    if encrypted:
        writer.encrypt("secret")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_pages_and_real_policy() -> None:
    blocks = extract_document(pdf_bytes(), filename="p.pdf", source_type="pdf")
    assert [(b.text, b.page) for b in blocks] == [("Returns policy", 1), ("Warranty policy", 2)]
    real = extract_document(POLICY.read_bytes(), filename=POLICY.name, source_type="pdf")
    assert {b.page for b in real} == set(range(1, 9))
    assert any(b.section_title and "Pagamento" in b.section_title for b in real)


def test_docx_sections_and_tables() -> None:
    document = Document()
    document.add_heading("Trocas", 1)
    document.add_paragraph("Prazo de sete dias.")
    document.add_heading("Garantia", 2)
    document.add_paragraph("Guarde a nota fiscal.")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Tabela"
    data = io.BytesIO()
    document.save(data)
    blocks = extract_document(data.getvalue(), filename="p.docx", source_type="docx")
    assert next(b for b in blocks if "sete dias" in b.text).section_title == "Trocas"
    assert next(b for b in blocks if "nota fiscal" in b.text).section_title == "Garantia"
    assert any(b.text == "Tabela" for b in blocks)


@pytest.mark.parametrize("kind,name", [("text", None), ("txt", "a.txt")])
def test_text_normalization(kind: str, name: str | None) -> None:
    blocks = extract_document(
        "  cafe\u0301\t  válido\x01 \n\n".encode() + b"\xff", source_type=kind, filename=name
    )
    assert blocks[0].text == "café válido\n\n�"


def test_markdown_sections() -> None:
    blocks = extract_document(
        b"# Trocas\nSete dias\n## Garantia\nUm ano", filename="p.md", source_type="md"
    )
    assert [b.section_title for b in blocks] == ["Trocas", "Garantia"]


@pytest.mark.parametrize(
    "data,name,kind",
    [
        (b"fake", "a.pdf", "pdf"),
        (b"fake", "a.docx", "docx"),
        (b"abc", "a.exe", "txt"),
        (b"%PDF-fake", "a.txt", "txt"),
        (b"PK\x03\x04broken", "a.docx", "docx"),
        (b"abc\x00", "a.txt", "txt"),
        (b"", None, "text"),
        (b"abc", None, "html"),
        (b"%PDF-broken", "a.pdf", "pdf"),
    ],
)
def test_reject_invalid(data: bytes, name: str | None, kind: str) -> None:
    with pytest.raises(ValueError):
        extract_document(data, filename=name, source_type=kind)


def test_size_limits_encryption_and_zip_bombs() -> None:
    with pytest.raises(ValueError, match="tamanho"):
        extract_document(b"abcdef", settings=Settings(max_upload_bytes=5))
    with pytest.raises(ValueError, match="tamanho"):
        extract_document(b"abcdef", settings=Settings(max_text_chars=5))
    with pytest.raises(ValueError, match="criptografado"):
        extract_document(pdf_bytes(encrypted=True), filename="p.pdf", source_type="pdf")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "x" * 50_000)
    with pytest.raises(ValueError, match="descompactado"):
        extract_document(
            data.getvalue(),
            filename="p.docx",
            source_type="docx",
            settings=Settings(max_upload_bytes=1000),
        )
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("file.txt", "wrong")
    with pytest.raises(ValueError, match="DOCX"):
        extract_document(data.getvalue(), filename="p.docx", source_type="docx")


def test_chunk_size_overlap_sections_and_pages() -> None:
    encoding = tokenizer()
    blocks = [
        TextBlock(" política musical" * 100, "Trocas", 1),
        TextBlock(" devoluções" * 100, "Trocas", 2),
        TextBlock("Garantia.", "Garantia", 3),
    ]
    chunks = chunk_blocks(blocks, Settings(chunk_size_tokens=100, chunk_overlap_tokens=20))
    assert all(c.token_count <= 100 for c in chunks)
    assert encoding.encode(chunks[0].content)[-20:] == encoding.encode(chunks[1].content)[:20]
    assert chunks[-1].section_title == "Garantia"
    assert chunks[-1].page_start == chunks[-1].page_end == 3
    assert any(c.page_start == 1 and c.page_end == 2 for c in chunks)
    assert chunk_blocks([]) == []


def test_chunk_unicode_boundaries() -> None:
    chunks = chunk_blocks(
        [TextBlock("🎸 漢字 música " * 150)],
        Settings(chunk_size_tokens=101, chunk_overlap_tokens=19),
    )
    assert all("�" not in c.content and c.token_count <= 101 for c in chunks)
