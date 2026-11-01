"""Tenant-scoped conversation orchestration within the trusted local boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable, Iterable
from typing import cast

import structlog
from agents import ModelProvider, ModelSettings, RunConfig, Runner
from agents.items import TResponseInputItem
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.customer_service import build_customer_service_agent
from app.agents.guardrails import (
    INJECTION_REFUSAL,
    NO_INFORMATION,
    SAFE_FALLBACK,
    check_input,
    check_output,
)
from app.agents.instructions import PROMPT_VERSION, build_instructions
from app.core.config import Settings
from app.integrations.local_commerce import LocalCommerceBackend
from app.models import (
    AgentSettings,
    AuditEvent,
    CommerceCustomer,
    Conversation,
    Message,
    MessageSource,
    ToolExecution,
)
from app.rag.citations import build_citations, format_sources_footer
from app.rag.embeddings import get_embedding_provider
from app.rag.types import EmbeddingProvider
from app.schemas.chat import ChatReply, ConversationNotFoundError, HandoffInfo, SourceRef
from app.services.model_gateway import GuardedModelProvider, build_model_provider
from app.services.privacy import PrivacyBoundaryError, redact_user_text, scan_outbound
from app.services.privacy_vault import TOKEN_RE, PrivacyVault
from app.services.vault_store import VaultStore
from app.tools.context import ToolCallRecord, ToolContext
from app.tools.handoff import request_human_handoff
from emporio_agent.privacy import redact_for_log

ModelProviderFactory = Callable[[Settings, Iterable[str]], ModelProvider]
logger = structlog.get_logger(__name__)


class _LazyEmbeddings:
    """Defer provider initialization into the guarded tool loop, even for missing keys."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dimensions = settings.embedding_dimensions
        self.model_name = settings.openai_embedding_model
        self.provider: EmbeddingProvider | None = None

    def _get(self) -> EmbeddingProvider:
        if self.provider is None:
            self.provider = get_embedding_provider(self.settings)
            self.model_name = self.provider.model_name
        return self.provider

    async def embed_query(self, text: str) -> list[float]:
        return await self._get().embed_query(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._get().embed_documents(texts)


def _tool_grounded(records: list[ToolCallRecord]) -> bool:
    for record in records:
        if record.status != "success" or record.tool_name in {
            "search_knowledge",
            "request_human_handoff",
        }:
            continue
        data = record.result_summary.get("data")
        if isinstance(data, dict) and data:
            if "items" in data and not data["items"]:
                continue
            if record.tool_name == "lookup_order" and not data.get("identity_verified"):
                continue
            return True
        if isinstance(data, list) and data:
            return True
    return False


async def _handoff(ctx: ToolContext, reason: str) -> None:
    if ctx.handoff_id is not None:
        return
    try:
        await request_human_handoff(ctx, reason, "Solicitação encaminhada para análise humana.")
    except Exception:
        # No success claim when even the handoff system is unavailable.
        ctx.tool_calls.append(
            ToolCallRecord(
                tool_name="request_human_handoff",
                arguments_redacted={"reason": reason},
                status="error",
                error_code="unavailable",
                result_summary={},
                latency_ms=0,
            )
        )


class ChatService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        vault_store: VaultStore,
        model_provider_factory: ModelProviderFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.vault_store = vault_store
        self.model_provider_factory = model_provider_factory or build_model_provider
        self.embedder = _LazyEmbeddings(settings)

    async def handle_message(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        text: str,
        correlation_id: str,
        channel: str = "api",
        external_user_ref: str | None = None,
    ) -> ChatReply:
        started = time.monotonic()
        # Never log an arbitrary caller-provided correlation string (it could contain PII).
        safe_correlation = hashlib.sha256(correlation_id.encode()).hexdigest()[:32]
        if conversation_id is None:
            conversation = Conversation(
                tenant_id=tenant_id,
                channel=channel,
                external_user_ref=external_user_ref,
            )
            session.add(conversation)
            await session.flush()
            conversation_id = conversation.id
        else:
            found = await session.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                )
            )
            if found is None:
                raise ConversationNotFoundError("Conversation not found")
            conversation = found
        agent_settings = await session.scalar(
            select(AgentSettings).where(AgentSettings.tenant_id == tenant_id)
        )
        if agent_settings is None:
            agent_settings = AgentSettings(
                tenant_id=tenant_id,
                assistant_name="Assistente",
                public_contact_values=[],
            )
        known_names = list(
            await session.scalars(
                select(CommerceCustomer.name).where(CommerceCustomer.tenant_id == tenant_id)
            )
        )
        vault_ok = True
        try:
            vault = await self.vault_store.load(tenant_id, conversation_id)
        except Exception:
            vault, vault_ok = PrivacyVault(), False
        previous_tokens = set(vault.values)
        # A customer cannot manufacture references to values stored in an earlier turn.
        redacted = redact_user_text(
            TOKEN_RE.sub("[referência inválida]", text),
            vault,
            known_restricted_values=known_names,
        )
        user_message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role="user",
            content=text,
            content_redacted=redacted,
            correlation_id=safe_correlation,
        )
        session.add(user_message)
        # Tools use independent sessions, so the conversation FK must be committed first.
        await session.commit()
        history = list(
            await session.scalars(
                select(Message)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.conversation_id == conversation_id,
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(20)
            )
        )
        inputs: list[TResponseInputItem] = []
        for message in reversed(history):
            content = message.content_redacted
            if message.id != user_message.id:
                content = TOKEN_RE.sub(
                    lambda match: (
                        match.group() if match.group() in previous_tokens else "[dado expirado]"
                    ),
                    content,
                )
            content = redact_user_text(content, vault, known_restricted_values=known_names)
            inputs.append(cast(TResponseInputItem, {"role": message.role, "content": content}))
        try:
            await self.vault_store.save(tenant_id, conversation_id, vault)
        except Exception:
            vault_ok = False
        ctx = ToolContext(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            correlation_id=safe_correlation,
            session_factory=self.session_factory,
            commerce=LocalCommerceBackend(self.session_factory, tenant_id),
            embedder=self.embedder,
            vault=vault,
        )
        allowlist = agent_settings.public_contact_values or []
        decision = check_input(text)
        answer = SAFE_FALLBACK
        failure_reason: str | None = None
        provider: ModelProvider | None = None
        if decision.refusal:
            answer = INJECTION_REFUSAL
        elif decision.reason:
            await _handoff(ctx, decision.reason)
            answer = "Entendo. Vou verificar como podemos ajudar com sua solicitação."
        elif not vault_ok:
            failure_reason = "privacy_guard"
        else:
            try:
                async with asyncio.timeout(self.settings.model_timeout_seconds):
                    provider = self.model_provider_factory(self.settings, allowlist)
                    # Injected test/custom providers cannot bypass the boundary either.
                    guarded = GuardedModelProvider(provider, allowlist)
                    agent = build_customer_service_agent(agent_settings)
                    result = await Runner.run(
                        agent,
                        inputs,
                        context=ctx,
                        max_turns=self.settings.max_agent_turns,
                        run_config=RunConfig(
                            model_provider=guarded,
                            model=agent_settings.chat_model or self.settings.openai_chat_model,
                            tracing_disabled=not self.settings.openai_tracing_enabled,
                            trace_include_sensitive_data=False,
                            model_settings=ModelSettings(store=False, parallel_tool_calls=False),
                        ),
                    )
                    answer = str(result.final_output)
            except PrivacyBoundaryError:
                failure_reason = "privacy_guard"
            except Exception:
                # Includes SDK MaxTurnsExceeded, provider/tool errors and TimeoutError.
                failure_reason = "tool_failure"
            finally:
                if provider is not None:
                    try:
                        await provider.aclose()
                    except Exception:
                        logger.warning("model.cleanup_failed", correlation_id=safe_correlation)
        citations = build_citations(ctx.retrieved_chunks)
        labels = {citation.label for citation in citations}
        if failure_reason is None and not check_output(
            answer,
            records=ctx.tool_calls,
            labels=labels,
            instructions=build_instructions(agent_settings),
        ):
            failure_reason = "low_confidence"
        # Raw personal data invented/copied by a model must never enter future model history.
        try:
            scan_outbound(answer, allowlist=allowlist)
        except PrivacyBoundaryError:
            failure_reason = "privacy_guard"
        authorized_tokens: set[str] = set()
        for record in ctx.tool_calls:
            if record.tool_name == "lookup_order" and record.status == "success":
                data = record.result_summary.get("data")
                if isinstance(data, dict) and data.get("identity_verified"):
                    authorized_tokens.update(m.group() for m in TOKEN_RE.finditer(json.dumps(data)))
        if any(
            m.group(1) in {"TRACKING", "DATE"} and m.group() not in authorized_tokens
            for m in TOKEN_RE.finditer(answer)
        ):
            failure_reason = "privacy_guard"
        cited = set(re.findall(r"\[(S\d+)\]", answer))
        selected = [citation for citation in citations if citation.label in cited]
        tool_grounded = _tool_grounded(ctx.tool_calls)
        rag_calls = [r for r in ctx.tool_calls if r.tool_name == "search_knowledge"]
        sufficient = any(
            isinstance(data := r.result_summary.get("data"), dict)
            and data.get("sufficient") is True
            for r in rag_calls
        )
        if rag_calls and not sufficient and not tool_grounded:
            answer, selected = NO_INFORMATION, []
        if failure_reason is None and any(r.status == "error" for r in ctx.tool_calls):
            failure_reason = "tool_failure"
        if failure_reason:
            answer, selected = SAFE_FALLBACK, []
            await _handoff(ctx, failure_reason)
        elif not decision.refusal and not decision.reason and not selected and not tool_grounded:
            answer = NO_INFORMATION
            await _handoff(ctx, "no_information")
        grounded = bool(selected or tool_grounded) and failure_reason is None
        if rag_calls and not selected and not tool_grounded:
            grounded = False
        try:
            await self.vault_store.save(tenant_id, conversation_id, vault)
        except Exception:
            answer, selected, grounded = SAFE_FALLBACK, [], False
            await _handoff(ctx, "privacy_guard")
        if ctx.handoff_id is not None:
            answer += "\nSolicitei atendimento humano para ajudar com sua solicitação."
            conversation.status = "handoff"
        footer = format_sources_footer(selected)
        if footer:
            answer += "\n\n" + footer
        # Citation metadata is untrusted too; it must not smuggle vault-token reveals.
        if any(
            m.group(1) in {"TRACKING", "DATE"} and m.group() not in authorized_tokens
            for m in TOKEN_RE.finditer(answer)
        ):
            answer, selected, grounded = SAFE_FALLBACK, [], False
            await _handoff(ctx, "privacy_guard")
        revealed = vault.reveal(answer)
        latency = int((time.monotonic() - started) * 1000)
        assistant = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role="assistant",
            content=revealed,
            content_redacted=answer,
            correlation_id=safe_correlation,
            meta={
                "grounded": grounded,
                "handoff": ctx.handoff_id is not None,
                "latency_ms": latency,
                "prompt_version": PROMPT_VERSION,
            },
        )
        session.add(assistant)
        await session.flush()
        for citation in selected:
            fields = citation.model_dump(exclude={"label"})
            session.add(
                MessageSource(
                    tenant_id=tenant_id,
                    message_id=assistant.id,
                    label=citation.label,
                    **fields,
                )
            )
        for record in ctx.tool_calls:
            session.add(
                ToolExecution(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    message_id=assistant.id,
                    tool_name=record.tool_name,
                    arguments_redacted=redact_for_log(
                        record.arguments_redacted,
                        known_restricted_values=known_names,
                    ),
                    result_summary=redact_for_log(
                        record.result_summary,
                        known_restricted_values=known_names,
                    ),
                    status=record.status,
                    error_code=record.error_code,
                    latency_ms=record.latency_ms,
                    correlation_id=safe_correlation,
                )
            )
            session.add(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type="tool.call",
                    actor="system",
                    resource_type="message",
                    resource_id=str(assistant.id),
                    correlation_id=safe_correlation,
                    data={
                        "tool": record.tool_name,
                        "status": record.status,
                        "error_code": record.error_code,
                    },
                )
            )
        await session.commit()
        logger.info(
            "chat.completed",
            tenant_id=str(tenant_id),
            conversation_id=str(conversation_id),
            correlation_id=safe_correlation,
            latency_ms=latency,
            grounded=grounded,
            prompt_version=PROMPT_VERSION,
        )
        return ChatReply(
            conversation_id=conversation_id,
            user_message_id=user_message.id,
            message_id=assistant.id,
            reply=revealed,
            sources=[SourceRef(**citation.model_dump()) for citation in selected],
            handoff=HandoffInfo(
                handoff_id=ctx.handoff_id, reason=ctx.handoff_reason or "tool_failure"
            )
            if ctx.handoff_id is not None
            else None,
            grounded=grounded,
            tools_used=[record.tool_name for record in ctx.tool_calls],
            tool_errors=sum(record.status == "error" for record in ctx.tool_calls),
            retrieved_chunks=len(ctx.retrieved_chunks),
            latency_ms=latency,
        )
