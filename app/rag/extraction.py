"""Bounded local extraction. Parser errors never include uploaded content."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

from docx import Document as DocxDocument
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.core.config import Settings, get_settings

MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
    "text": "text/plain",
}


class ExtractionError(ValueError):
    """Safe, fixed extraction error suitable for an API response."""


@dataclass(frozen=True)
class TextBlock:
    text: str
    section_title: str | None = None
    page: int | None = None


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    return "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in text.splitlines()).strip()


def validate_upload(
    data: bytes, filename: str | None, source_type: str, settings: Settings
) -> None:
    if not data or len(data) > settings.max_upload_bytes:
        raise ExtractionError("Documento vazio ou acima do limite de tamanho.")
    if source_type not in MIME_TYPES:
        raise ExtractionError("Tipo de documento não suportado.")
    if source_type != "text" and (
        not filename or PurePath(filename).suffix.lower() != f".{source_type}"
    ):
        raise ExtractionError("Extensão incompatível com o tipo de documento.")
    if source_type == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ExtractionError("PDF inválido.")
    elif source_type == "docx":
        if not data.startswith(b"PK\x03\x04"):
            raise ExtractionError("DOCX inválido.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if (
                    len(infos) > 2048
                    or sum(i.file_size for i in infos) > settings.max_upload_bytes * 10
                ):
                    raise ExtractionError("DOCX descompactado acima do limite.")
                if "word/document.xml" not in archive.namelist():
                    raise ExtractionError("DOCX inválido.")
                if any(i.flag_bits & 1 for i in infos):
                    raise ExtractionError("DOCX criptografado não suportado.")
        except zipfile.BadZipFile:
            raise ExtractionError("DOCX inválido.") from None
    elif (
        data.startswith((b"%PDF-", b"PK\x03\x04", b"\x89PNG", b"\xff\xd8", b"\x7fELF"))
        or b"\x00" in data
    ):
        raise ExtractionError("Conteúdo incompatível com documento de texto.")


def _sections(text: str, *, page: int | None = None, markdown: bool = False) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    title: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        pattern = r"^#{1,6}\s+(.+?)\s*#*$" if markdown else r"^(\d+(?:\.\d+)*\.?\s+\S.{2,100})$"
        heading = re.match(pattern, line)
        if heading:
            if lines:
                blocks.append(TextBlock("\n".join(lines), title, page))
            title = heading.group(1)
            lines = [title]
        else:
            lines.append(line)
    if lines:
        blocks.append(TextBlock("\n".join(lines), title, page))
    return blocks


def extract_document(
    data: bytes,
    *,
    filename: str | None = None,
    source_type: str = "text",
    settings: Settings | None = None,
) -> list[TextBlock]:
    settings = settings or get_settings()
    validate_upload(data, filename, source_type, settings)
    blocks: list[TextBlock] = []
    try:
        if source_type == "pdf":
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ExtractionError("PDF criptografado não suportado.")
            length = 0
            previous_section: str | None = None
            for page_number, page in enumerate(reader.pages, 1):
                try:
                    text = page.extract_text(extraction_mode="layout") or ""
                except Exception:
                    text = page.extract_text() or ""
                length += len(text)
                if length > settings.max_text_chars:
                    raise ExtractionError("Texto acima do limite de tamanho.")
                cleaned = normalize_text(text)
                cleaned = "\n".join(
                    line
                    for line in cleaned.splitlines()
                    if line != "Empório da Música Manual de Políticas e Procedimentos"
                    and not re.fullmatch(r"Página \d+", line)
                )
                for block in _sections(cleaned, page=page_number):
                    previous_section = block.section_title or previous_section
                    blocks.append(TextBlock(block.text, previous_section, block.page))
        elif source_type == "docx":
            doc = DocxDocument(io.BytesIO(data))
            title = None
            for item in doc.iter_inner_content():
                if isinstance(item, Paragraph):
                    if item.style and item.style.name and item.style.name.startswith("Heading"):
                        title = normalize_text(item.text)
                    blocks.append(TextBlock(item.text, title))
                else:
                    for row in item.rows:
                        blocks.append(TextBlock(" | ".join(cell.text for cell in row.cells), title))
        else:
            text = data.decode("utf-8-sig", errors="replace")
            blocks = (
                _sections(normalize_text(text), markdown=source_type == "md")
                if source_type == "md"
                else [TextBlock(text)]
            )
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("Não foi possível ler o documento.") from None
    if sum(len(block.text) for block in blocks) > settings.max_text_chars:
        raise ExtractionError("Texto acima do limite de tamanho.")
    normalized = [TextBlock(normalize_text(b.text), b.section_title, b.page) for b in blocks]
    result = [b for b in normalized if b.text]
    if not result:
        raise ExtractionError("Documento sem texto extraível.")
    return result
