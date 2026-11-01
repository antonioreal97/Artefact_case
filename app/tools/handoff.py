"""Human handoff tool: opens a HandoffRequest, flags the conversation and audits it.

Idempotent within one turn: once ``ctx.handoff_id`` is set, further calls return the same
view without writing again. (Two calls racing concurrently in the same turn are not
serialized; the agent is expected to call this tool once.)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.handoff import HANDOFF_REASONS, AuditEvent, HandoffRequest
from app.schemas.tools import ToolResult
from app.tools.catalog import run_tool
from app.tools.context import ToolContext
from emporio_agent.privacy import redact_for_log

MAX_SUMMARY_CHARS = 2000
MSG_INVALID_REASON = "Motivo de encaminhamento inválido."
MSG_NO_CONVERSATION = "Não encontrei esta conversa para encaminhar ao atendimento humano."


class HandoffInput(BaseModel):
    reason: str = Field(max_length=32)
    summary: str = Field(default="", max_length=4 * MAX_SUMMARY_CHARS)


class HandoffView(BaseModel):
    reason: str
    status: Literal["open"] = "open"


async def request_human_handoff(
    ctx: ToolContext, reason: str, summary: str
) -> ToolResult[HandoffView]:
    """Escalate the conversation to a human agent (reason must be one of HANDOFF_REASONS)."""
    raw: dict[str, Any] = {"reason": reason, "summary": summary}
    args: dict[str, Any] = {
        "reason": reason,
        "summary_chars": len(summary) if isinstance(summary, str) else 0,
    }

    async def op() -> ToolResult[HandoffView]:
        if ctx.handoff_id is not None:
            return ToolResult.success(HandoffView(reason=ctx.handoff_reason or reason))
        params = HandoffInput.model_validate(raw)
        if params.reason not in HANDOFF_REASONS:
            return ToolResult.failure("invalid_input", MSG_INVALID_REASON)
        stored_summary = str(redact_for_log(params.summary.strip()))[:MAX_SUMMARY_CHARS]

        async with ctx.session_factory() as session:
            conversation = await session.scalar(
                select(Conversation)
                .where(
                    Conversation.id == ctx.conversation_id,
                    Conversation.tenant_id == ctx.tenant_id,
                )
                .with_for_update()
            )
            if conversation is None:
                return ToolResult.failure("not_found", MSG_NO_CONVERSATION)
            conversation.status = "handoff"
            handoff = HandoffRequest(
                tenant_id=ctx.tenant_id,
                conversation_id=ctx.conversation_id,
                reason=params.reason,
                summary=stored_summary,
                status="open",
            )
            session.add(handoff)
            await session.flush()
            session.add(
                AuditEvent(
                    tenant_id=ctx.tenant_id,
                    event_type="handoff.created",
                    actor="agent",
                    resource_type="handoff_request",
                    resource_id=str(handoff.id),
                    correlation_id=ctx.correlation_id,
                    data={"reason": params.reason, "conversation_id": str(ctx.conversation_id)},
                )
            )
            await session.commit()

        ctx.handoff_id = handoff.id
        ctx.handoff_reason = params.reason
        return ToolResult.success(HandoffView(reason=params.reason))

    return await run_tool(
        ctx,
        "request_human_handoff",
        args,
        op,
        lambda data: {"handoff_created": True, "reason": data.reason},
    )
