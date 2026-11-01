"""HTTP request/response models for the public API (``ChatReply`` lives in
``app.schemas.chat`` and ``IngestionReceipt`` in ``app.rag.types``; not duplicated here).

Request models ignore unknown fields: a ``tenant_id`` sent by a client is silently dropped -
the tenant comes only from the API key. Text fields containing a NUL character (``\x00``)
are rejected with 422: PostgreSQL ``text`` cannot store it (adversarial FINDING-6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.handoff import HANDOFF_REASONS

HandoffReason = Literal[
    "customer_request",
    "no_information",
    "low_confidence",
    "conflicting_sources",
    "sensitive_topic",
    "tool_failure",
    "privacy_guard",
]
assert set(get_args(HandoffReason)) == set(HANDOFF_REASONS)  # keep in sync with the model

HandoffStatus = Literal["open", "in_progress", "resolved"]


NUL_MESSAGE = "NUL characters are not allowed"


class _Request(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def _reject_nul(cls, value: object) -> object:
        if isinstance(value, str) and "\x00" in value:
            raise ValueError(NUL_MESSAGE)
        return value


class ErrorResponse(BaseModel):
    error: str = Field(examples=["not_found"])
    detail: str | list[dict[str, object]] | None = Field(default=None, examples=["Not found"])
    correlation_id: str | None = Field(default=None, examples=["3f2b8c0e9d1a4b7c"])


# --- health -----------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessOut(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "fail"]] = Field(examples=[{"database": "ok", "redis": "ok"}])


# --- chat -------------------------------------------------------------------------------


class ChatMessageRequest(_Request):
    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {"message": "Qual é o prazo para troca de um produto?"},
                {
                    "conversation_id": "0b8f2c1e-9a51-4f43-8d0e-6c3b8d7f1a22",
                    "message": "E se o produto veio com defeito?",
                },
            ]
        },
    )

    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=4000)


# --- conversations ----------------------------------------------------------------------


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: str
    status: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    handoff: bool = False
    grounded: bool | None = None


class MessageSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    message_id: uuid.UUID
    label: str
    document_id: uuid.UUID | None
    document_version_id: uuid.UUID | None
    chunk_id: uuid.UUID | None
    title: str
    section: str | None
    page: int | None
    url: str | None
    score: float
    quote: str


# --- knowledge --------------------------------------------------------------------------


class DocumentTextRequest(_Request):
    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "title": "Política de trocas",
                    "content": "Trocas podem ser solicitadas em até 7 dias corridos...",
                    "category": "politicas",
                }
            ]
        },
    )

    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    category: str | None = Field(default=None, max_length=100)
    source_url: str | None = Field(default=None, max_length=1000)


class DocumentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    version: int
    status: str = Field(validation_alias="ingestion_status")
    error_message: str | None
    mime_type: str
    byte_size: int
    page_count: int | None
    chunk_count: int | None
    created_at: datetime
    processed_at: datetime | None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    source_type: str
    filename: str | None
    source_url: str | None
    category: str | None
    status: str
    current_version_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class DocumentDetailOut(DocumentOut):
    versions: list[DocumentVersionOut] = Field(default_factory=list)


# --- handoffs ---------------------------------------------------------------------------


class HandoffCreateRequest(_Request):
    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "conversation_id": "0b8f2c1e-9a51-4f43-8d0e-6c3b8d7f1a22",
                    "reason": "customer_request",
                    "summary": "Cliente pediu para falar com um atendente sobre uma troca.",
                }
            ]
        },
    )

    conversation_id: uuid.UUID
    reason: HandoffReason
    summary: str = Field(default="", max_length=2000)


class HandoffUpdateRequest(_Request):
    model_config = ConfigDict(
        extra="ignore", json_schema_extra={"examples": [{"status": "in_progress"}]}
    )

    status: HandoffStatus


class HandoffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    reason: str
    summary: str
    status: str
    created_at: datetime
    updated_at: datetime
