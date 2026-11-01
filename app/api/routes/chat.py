"""Chat endpoint: one customer turn in, one ``ChatReply`` out."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import ChatServiceDep, Principal, SessionDep, require_scope
from app.core.logging import bind_conversation_id, get_logger
from app.core.metrics import CHAT_TURNS, HANDOFFS, MODEL_ERRORS, RETRIEVAL, TOOL_ERRORS
from app.schemas.api import ChatMessageRequest, ErrorResponse
from app.schemas.chat import ChatReply, ConversationNotFoundError

router = APIRouter(prefix="/v1/chat", tags=["chat"])
log = get_logger(__name__)

_INSUFFICIENT_REASONS = frozenset({"no_information", "low_confidence"})


def record_turn_metrics(reply: ChatReply, channel: str) -> None:
    CHAT_TURNS.inc(channel=channel)
    if reply.tool_errors:
        TOOL_ERRORS.inc(reply.tool_errors, channel=channel)
    if reply.retrieved_chunks:
        RETRIEVAL.inc(result="hit")
    if reply.handoff is not None:
        HANDOFFS.inc(reason=reply.handoff.reason, origin="agent")
        if reply.handoff.reason in _INSUFFICIENT_REASONS:
            RETRIEVAL.inc(result="insufficient")


@router.post(
    "/messages",
    response_model=ChatReply,
    responses={404: {"model": ErrorResponse, "description": "Conversation not found"}},
)
async def post_message(
    body: ChatMessageRequest,
    request: Request,
    session: SessionDep,
    chat: ChatServiceDep,
    principal: Annotated[Principal, Depends(require_scope("chat"))],
) -> ChatReply:
    """Send a customer message (scope `chat`). Omit `conversation_id` to start a new one.

    Request: `{"message": "Qual é o prazo para troca?"}`

    Response (abridged): `{"conversation_id": "…", "user_message_id": "…",
    "message_id": "…", "reply": "Você pode solicitar a troca em até 7 dias… [S1]",
    "sources": [{"label": "S1", "document_id": "…", "title": "Políticas da loja",
    "score": 0.82}], "handoff": null, "grounded": true, "tools_used": ["search_knowledge"],
    "tool_errors": 0, "retrieved_chunks": 3, "latency_ms": 840}`

    A `conversation_id` belonging to another tenant yields 404 (same as unknown)."""
    if body.conversation_id is not None:
        bind_conversation_id(body.conversation_id)
    try:
        reply = await chat.handle_message(
            session,
            tenant_id=principal.tenant_id,
            conversation_id=body.conversation_id,
            text=body.message,
            channel="api",
            external_user_ref=None,
            correlation_id=request.state.correlation_id,
        )
    except ConversationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found") from None
    except Exception as exc:
        MODEL_ERRORS.inc(error_type=type(exc).__name__)
        raise
    bind_conversation_id(reply.conversation_id)
    record_turn_metrics(reply, "api")
    log.info(
        "chat.turn_completed",
        message_id=str(reply.message_id),
        grounded=reply.grounded,
        handoff=reply.handoff is not None,
        tools_used=reply.tools_used,
        tool_errors=reply.tool_errors,
        retrieved_chunks=reply.retrieved_chunks,
        latency_ms=reply.latency_ms,
    )
    return reply
