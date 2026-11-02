from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.database import Base
from app.models.base import TenantScoped, Timestamps, UUIDPk


class Tenant(UUIDPk, Timestamps, Base):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended


class ApiKey(UUIDPk, Timestamps, TenantScoped, Base):
    """Hashed API credential. The plaintext key is shown once at creation and never stored.

    Format: ``emp_<prefix>_<secret>``; lookup by ``key_prefix`` then constant-time compare
    of ``key_hash = sha256(pepper + full_key)``."""

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    # Scopes: "chat", "knowledge:write", "knowledge:read", "handoffs", "conversations:read"
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentSettings(Timestamps, Base):
    """Per-tenant assistant configuration (persona, tone, privacy allowlist)."""

    __tablename__ = "agent_settings"
    __table_args__ = (
        CheckConstraint("char_length(welcome_message) <= 1000", name="ck_welcome_message_length"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    assistant_name: Mapped[str] = mapped_column(String(100))
    business_description: Mapped[str] = mapped_column(Text, default="")
    tone_of_voice: Mapped[str] = mapped_column(Text, default="")
    default_language: Mapped[str] = mapped_column(String(16), default="pt-BR")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    welcome_message: Mapped[str | None] = mapped_column(Text)
    handoff_message: Mapped[str] = mapped_column(Text, default="")
    # Public business contact values (store phone/email/address) that the privacy scanner
    # must NOT treat as customer PII when they appear in retrieved documents.
    public_contact_values: Mapped[list[str]] = mapped_column(JSON, default=list)
    retrieval_min_similarity: Mapped[float | None] = mapped_column(Float)
    chat_model: Mapped[str | None] = mapped_column(String(100))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    @validates("welcome_message")
    def validate_welcome_message(self, key: str, value: str | None) -> str | None:
        if value is not None and len(value) > 1000:
            raise ValueError("welcome_message must contain at most 1000 characters")
        return value


class TenantChannel(UUIDPk, Timestamps, TenantScoped, Base):
    """Maps an external channel identity (e.g. WhatsApp phone_number_id) to a tenant."""

    __tablename__ = "tenant_channels"
    __table_args__ = (UniqueConstraint("channel", "external_id"),)

    channel: Mapped[str] = mapped_column(String(32))  # "whatsapp"
    external_id: Mapped[str] = mapped_column(String(128))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
