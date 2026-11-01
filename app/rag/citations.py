"""Pure citation rendering; input order preserves labels across multiple searches."""

from __future__ import annotations

from app.rag.types import Citation, RetrievedChunk


def build_citations(chunks: list[RetrievedChunk]) -> list[Citation]:
    unique: list[RetrievedChunk] = []
    seen = set()
    for chunk in chunks:
        if chunk.chunk_id not in seen:
            unique.append(chunk)
            seen.add(chunk.chunk_id)
    return [
        Citation(
            label=f"S{index}",
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            document_version_id=chunk.document_version_id,
            title=chunk.document_title,
            section=chunk.section_title,
            page=chunk.page_start,
            url=chunk.source_url,
            score=chunk.score,
            quote=" ".join(chunk.content.split())[:240],
        )
        for index, chunk in enumerate(unique, 1)
    ]


def format_sources_footer(citations: list[Citation]) -> str:
    if not citations:
        return ""
    lines = ["Fontes:"]
    for citation in citations:
        source = f"[{citation.label}] {citation.title}"
        if citation.section:
            section = (
                citation.section if citation.section.startswith("§") else "§" + citation.section
            )
            source += " — " + section
        if citation.page is not None:
            source += f", p. {citation.page}"
        lines.append(source)
    return "\n".join(lines)
