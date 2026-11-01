"""Run context shared by every tool call of one agent turn (trusted, local only).

The tenant is fixed here from authentication; tools never take a tenant id argument."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.base import CommerceBackend
from app.rag.types import EmbeddingProvider, RetrievedChunk
from app.services.privacy_vault import PrivacyVault


@dataclass
class ToolCallRecord:
    tool_name: str
    arguments_redacted: dict[str, object]
    status: str  # success | error
    error_code: str | None
    result_summary: dict[str, object]
    latency_ms: int


@dataclass
class ToolContext:
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    correlation_id: str
    # Tools open their OWN short-lived session from this factory (the SDK may run tool
    # calls concurrently; one AsyncSession must never be shared across tasks).
    session_factory: async_sessionmaker[AsyncSession]
    commerce: CommerceBackend
    embedder: EmbeddingProvider
    vault: PrivacyVault
    # Filled during the turn; persisted by the chat service afterwards.
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    handoff_id: uuid.UUID | None = None
    handoff_reason: str | None = None
