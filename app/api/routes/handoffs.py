"""Human handoff requests (scope ``handoffs``). Tenant-scoped in SQL; foreign ids -> 404."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.dependencies import Principal, SessionDep, require_scope
from app.core.logging import bind_conversation_id, get_logger
from app.core.metrics import HANDOFFS
from app.models import AuditEvent, Conversation, HandoffRequest
from app.schemas.api import (
    ErrorResponse,
    HandoffCreateRequest,
    HandoffOut,
    HandoffStatus,
    HandoffUpdateRequest,
)
from emporio_agent.privacy import redact_for_log

router = APIRouter(prefix="/v1/handoffs", tags=["handoffs"])
log = get_logger(__name__)

HandoffsDep = Annotated[Principal, Depends(require_scope("handoffs"))]
_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found for this tenant"}
}


async def _get_handoff(
    session: SessionDep, tenant_id: uuid.UUID, handoff_id: uuid.UUID
) -> HandoffRequest:
    handoff = await session.scalar(
        select(HandoffRequest).where(
            HandoffRequest.id == handoff_id, HandoffRequest.tenant_id == tenant_id
        )
    )
    if handoff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Handoff not found")
    return handoff


def _audit(
    request: Request, principal: Principal, event_type: str, handoff: HandoffRequest
) -> AuditEvent:
    return AuditEvent(
        tenant_id=principal.tenant_id,
        event_type=event_type,
        actor=str(principal.api_key_id),
        resource_type="handoff",
        resource_id=str(handoff.id),
        correlation_id=request.state.correlation_id,
        data={"status": handoff.status, "reason": handoff.reason},
    )


@router.post("", response_model=HandoffOut, status_code=201, responses=_NOT_FOUND)
async def create_handoff(
    body: HandoffCreateRequest, request: Request, session: SessionDep, principal: HandoffsDep
) -> HandoffOut:
    """Escalate a conversation to a human. The summary is stored redacted.

    Request: `{"conversation_id": "…", "reason": "customer_request",
    "summary": "Cliente pediu atendimento humano sobre uma troca."}`
    Response 201: `{"id": "…", "conversation_id": "…", "reason": "customer_request",
    "summary": "…", "status": "open", "created_at": "…", "updated_at": "…"}`"""
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == body.conversation_id,
            Conversation.tenant_id == principal.tenant_id,
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    bind_conversation_id(conversation.id)
    handoff = HandoffRequest(
        tenant_id=principal.tenant_id,
        conversation_id=conversation.id,
        reason=body.reason,
        summary=redact_for_log(body.summary),
        status="open",
    )
    session.add(handoff)
    conversation.status = "handoff"
    await session.flush()
    session.add(_audit(request, principal, "handoff.created", handoff))
    await session.commit()
    await session.refresh(handoff)
    HANDOFFS.inc(reason=body.reason, origin="api")
    log.info("handoff.created", handoff_id=str(handoff.id), reason=body.reason)
    return HandoffOut.model_validate(handoff)


@router.get("", response_model=list[HandoffOut])
async def list_handoffs(
    session: SessionDep,
    principal: HandoffsDep,
    status_filter: Annotated[HandoffStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[HandoffOut]:
    """Newest first; optional `?status=open|in_progress|resolved`."""
    query = select(HandoffRequest).where(HandoffRequest.tenant_id == principal.tenant_id)
    if status_filter is not None:
        query = query.where(HandoffRequest.status == status_filter)
    rows = await session.scalars(
        query.order_by(HandoffRequest.created_at.desc()).limit(limit).offset(offset)
    )
    return [HandoffOut.model_validate(h) for h in rows]


@router.get("/{handoff_id}", response_model=HandoffOut, responses=_NOT_FOUND)
async def get_handoff(
    handoff_id: uuid.UUID, session: SessionDep, principal: HandoffsDep
) -> HandoffOut:
    return HandoffOut.model_validate(await _get_handoff(session, principal.tenant_id, handoff_id))


@router.patch("/{handoff_id}", response_model=HandoffOut, responses=_NOT_FOUND)
async def update_handoff(
    handoff_id: uuid.UUID,
    body: HandoffUpdateRequest,
    request: Request,
    session: SessionDep,
    principal: HandoffsDep,
) -> HandoffOut:
    """Request: `{"status": "in_progress"}` (one of open, in_progress, resolved)."""
    handoff = await _get_handoff(session, principal.tenant_id, handoff_id)
    handoff.status = body.status
    session.add(_audit(request, principal, "handoff.updated", handoff))
    await session.commit()
    await session.refresh(handoff)
    log.info("handoff.updated", handoff_id=str(handoff.id), status=handoff.status)
    return HandoffOut.model_validate(handoff)
