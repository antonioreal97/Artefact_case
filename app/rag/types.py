"""RAG contracts shared by ingestion, retrieval, the agent and the API."""

from __future__ import annotations

import uuid
from typing import Protocol

from pydantic import BaseModel


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class RetrievalFilters(BaseModel):
    document_ids: list[uuid.UUID] | None = None
    categories: list[str] | None = None
    # Only "active" documents are searchable by default.
    document_status: str = "active"


class RetrievedChunk(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    document_title: str
    category: str | None
    source_url: str | None
    section_title: str | None
    page_start: int | None
    page_end: int | None
    content: str
    vector_similarity: float | None  # cosine similarity in [−1, 1]; None if FTS-only hit
    text_rank: float | None  # ts_rank_cd; None if vector-only hit
    score: float  # fused (RRF) score used for ordering


class RetrievalResult(BaseModel):
    query: str
    chunks: list[RetrievedChunk]  # already filtered by tenant, status and min relevance
    best_similarity: float | None
    sufficient: bool  # False => caller must answer safely / hand off, never improvise


class Citation(BaseModel):
    label: str  # "S1", "S2", ... stable within one answer
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    title: str
    section: str | None
    page: int | None
    url: str | None
    score: float
    quote: str  # <= ~240 chars excerpt from the chunk


class IngestionReceipt(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    version: int
    status: str  # pending | processing | completed | failed
    duplicate: bool  # True when identical content already existed for this document
