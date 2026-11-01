from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TenantScoped, Timestamps, UUIDPk, utcnow

HANDOFF_REASONS = (
    "customer_request",
    "no_information",
    "low_confidence",
    "conflicting_sources",
    "sensitive_topic",  # complaints, payments, commercial exceptions
    "tool_failure",
    "privacy_guard",
)


class HandoffRequest(UUIDPk, Timestamps, TenantScoped, Base):
    __tablename__ = "handoff_requests"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(String(32))  # one of HANDOFF_REASONS
    summary: Mapped[str] = mapped_column(Text, default="")  # redacted summary
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|in_progress|resolved
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditEvent(UUIDPk, Timestamps, Base):
    """Security/audit trail. tenant_id nullable only for pre-auth events (bad API key)."""

    __tablename__ = "audit_events"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str | None] = mapped_column(String(64))  # api key id / "system"
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # redacted only
