"""Token windows confined to sections; overlapping windows retain page provenance."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from app.core.config import Settings, get_settings
from app.rag.extraction import TextBlock


@lru_cache(maxsize=1)
def tokenizer() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


@dataclass(frozen=True)
class TextChunk:
    content: str
    token_count: int
    section_title: str | None
    page_start: int | None
    page_end: int | None


def chunk_blocks(blocks: list[TextBlock], settings: Settings | None = None) -> list[TextChunk]:
    settings = settings or get_settings()
    encoding = tokenizer()
    groups: list[list[TextBlock]] = []
    for block in blocks:
        if groups and groups[-1][-1].section_title == block.section_title:
            groups[-1].append(block)
        else:
            groups.append([block])
    result: list[TextChunk] = []
    for group in groups:
        tokens: list[int] = []
        pages: list[int | None] = []
        for block in group:
            encoded = encoding.encode(
                ("\n\n" if tokens else "") + block.text, disallowed_special=()
            )
            tokens.extend(encoded)
            pages.extend([block.page] * len(encoded))
        start = 0
        while start < len(tokens):
            end = min(start + settings.chunk_size_tokens, len(tokens))
            # A token boundary may split a UTF-8 character. Move the edge inward.
            while end > start:
                try:
                    content = encoding.decode_bytes(tokens[start:end]).decode("utf-8")
                    break
                except UnicodeDecodeError:
                    end -= 1
            else:
                raise ValueError("Não foi possível segmentar o texto.")
            chunk_pages = [p for p in pages[start:end] if p is not None]
            result.append(
                TextChunk(
                    content,
                    end - start,
                    group[0].section_title,
                    min(chunk_pages) if chunk_pages else None,
                    max(chunk_pages) if chunk_pages else None,
                )
            )
            if end == len(tokens):
                break
            next_start = max(start + 1, end - settings.chunk_overlap_tokens)
            while next_start < end:
                try:
                    encoding.decode_bytes(tokens[next_start:end]).decode("utf-8")
                    break
                except UnicodeDecodeError:
                    next_start += 1
            start = next_start
    return result
