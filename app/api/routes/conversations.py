"""Conversation history and citation sources (scope ``conversations:read``).

Every query filters by the authenticated tenant in SQL; ids of other tenants are
indistinguishable from unknown ids (404)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.dependencies import Principal, SessionDep, require_scope
from app.models import Conversation, Message, MessageSource
from app.schemas.api import ConversationOut, ErrorResponse, MessageOut, MessageSourceOut

router = APIRouter(prefix="/v1", tags=["conversations"])

ReaderDep = Annotated[Principal, Depends(require_scope("conversations:read"))]
_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found for this tenant"}
}


async def _get_conversation(
    session: SessionDep, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.tenant_id == tenant_id
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@router.get(
    "/conversations/{conversation_id}", response_model=ConversationOut, responses=_NOT_FOUND
)
async def get_conversation(
    conversation_id: uuid.UUID, session: SessionDep, principal: ReaderDep
) -> ConversationOut:
    """Example: `{"id": "…", "channel": "api", "status": "open",
    "created_at": "2026-10-31T12:00:00Z", "updated_at": "2026-10-31T12:01:10Z"}`"""
    conversation = await _get_conversation(session, principal.tenant_id, conversation_id)
    return ConversationOut.model_validate(conversation)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
    responses=_NOT_FOUND,
)
async def list_messages(
    conversation_id: uuid.UUID,
    session: SessionDep,
    principal: ReaderDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MessageOut]:
    """Chronological history. Example item: `{"id": "…", "conversation_id": "…",
    "role": "assistant", "content": "Você pode trocar em até 7 dias… [S1]",
    "created_at": "…", "handoff": false, "grounded": true}`"""
    await _get_conversation(session, principal.tenant_id, conversation_id)
    rows = await session.scalars(
        select(Message)
        .where(Message.tenant_id == principal.tenant_id, Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
        .limit(limit)
        .offset(offset)
    )
    return [
        MessageOut(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
            handoff=bool((m.meta or {}).get("handoff", False)),
            grounded=(m.meta or {}).get("grounded"),
        )
        for m in rows
    ]


@router.get(
    "/messages/{message_id}/sources",
    response_model=list[MessageSourceOut],
    responses=_NOT_FOUND,
)
async def list_message_sources(
    message_id: uuid.UUID, session: SessionDep, principal: ReaderDep
) -> list[MessageSourceOut]:
    """Citations recorded for an assistant message. Example item: `{"label": "S1",
    "title": "Políticas da loja", "section": "Trocas", "page": 2, "score": 0.82,
    "quote": "Trocas podem ser solicitadas em até 7 dias…", …}`"""
    exists = await session.scalar(
        select(Message.id).where(Message.id == message_id, Message.tenant_id == principal.tenant_id)
    )
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Message not found")
    rows = await session.scalars(
        select(MessageSource)
        .where(
            MessageSource.tenant_id == principal.tenant_id,
            MessageSource.message_id == message_id,
        )
        .order_by(MessageSource.label)
    )
    return [MessageSourceOut.model_validate(s) for s in rows]
