"""Chat contract between ChatService (Task E) and the API/channels (Task F). Frozen."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    label: str  # "S1"
    document_id: uuid.UUID
    title: str
    section: str | None = None
    page: int | None = None
    url: str | None = None
    score: float


class HandoffInfo(BaseModel):
    handoff_id: uuid.UUID
    reason: str  # one of app.models.handoff.HANDOFF_REASONS


class ChatReply(BaseModel):
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    message_id: uuid.UUID  # assistant message
    reply: str  # final text shown to the customer (tokens revealed, sources footer included)
    # Render before reply. Greeting-only first contact instead puts the welcome in
    # reply and leaves this null; later turns and tenants without a welcome use null.
    welcome_message: str | None = Field(
        default=None,
        description=(
            "Render before reply on first contact with a question. For a greeting-only "
            "first contact, reply contains the welcome and this is null; tools_used is "
            "empty, grounded is false and handoff is null. Null on later turns."
        ),
    )
    route: str | None = None  # optional routing telemetry; absent configuration keeps null
    sources: list[SourceRef] = Field(default_factory=list)
    handoff: HandoffInfo | None = None
    grounded: bool  # True if every factual claim came from a tool result or cited source
    tools_used: list[str] = Field(default_factory=list)
    tool_errors: int = 0
    retrieved_chunks: int = 0
    latency_ms: int = 0


class ConversationNotFoundError(LookupError):
    """conversation_id unknown FOR THIS TENANT (also raised for other tenants' ids)."""


# ChatService.handle_message(session, *, tenant_id, conversation_id, text, channel="api",
#     external_user_ref=None, correlation_id) -> ChatReply   (implemented in app/services/chat.py)
