from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TenantScoped, Timestamps, UUIDPk, utcnow


class Conversation(UUIDPk, Timestamps, TenantScoped, Base):
    __tablename__ = "conversations"

    channel: Mapped[str] = mapped_column(String(32), default="api")  # api | whatsapp
    # Opaque/hashed reference of the end user on the channel (never a raw phone number).
    external_user_ref: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | handoff | closed
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Message(UUIDPk, Timestamps, TenantScoped, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    # Raw text: stays inside the trusted local boundary (DB), returned only to the tenant.
    content: Mapped[str] = mapped_column(Text)
    # Tokenized/redacted text: the ONLY form ever sent to an external model.
    content_redacted: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    # e.g. {"handoff": bool, "grounded": bool, "latency_ms": int, "model": str}
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class MessageSource(UUIDPk, Timestamps, TenantScoped, Base):
    """A knowledge chunk cited by an assistant message (verifiable citation record)."""

    __tablename__ = "message_sources"

    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("chunks.id", ondelete="SET NULL")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="SET NULL")
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    label: Mapped[str] = mapped_column(String(16))  # e.g. "S1"
    title: Mapped[str] = mapped_column(String(300))
    section: Mapped[str | None] = mapped_column(String(300))
    page: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String(1000))
    score: Mapped[float] = mapped_column(Float)
    quote: Mapped[str] = mapped_column(Text)  # short excerpt of the chunk


class ToolExecution(UUIDPk, Timestamps, TenantScoped, Base):
    """Audit trail of every tool call. Arguments/results stored redacted only."""

    __tablename__ = "tool_executions"

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16))  # success | error
    error_code: Mapped[str | None] = mapped_column(String(64))
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
