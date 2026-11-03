"""Tenant-scoped conversation orchestration within the trusted local boundary."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from collections.abc import Callable, Iterable
from typing import cast

import structlog
from agents import ModelProvider, ModelSettings, RunConfig, Runner
from agents.items import TResponseInputItem
from openai.types.shared import Reasoning
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.customer_service import build_customer_service_agent
from app.agents.guardrails import (
    HANDOFF_ACK,
    HIDDEN_VALUE,
    INJECTION_REFUSAL,
    NO_INFORMATION,
    PRIVACY_NOTICE,
    SAFE_FALLBACK,
    admits_no_information,
    answer_uses_tool_facts,
    check_input,
    discusses_sensitive_topic,
    has_factual_claim,
    is_greeting_only,
    is_out_of_scope_refusal,
    output_violation,
    verified_lookup_tokens,
)
from app.agents.instructions import POLICY_TEXT, PROMPT_VERSION, build_instructions
from app.core.config import Settings
from app.integrations.local_commerce import LocalCommerceBackend
from app.models import (
    AgentSettings,
    AuditEvent,
    CommerceCustomer,
    Conversation,
    Message,
    MessageSource,
    Tenant,
    ToolExecution,
)
from app.models.handoff import HANDOFF_REASONS, HandoffRequest
from app.rag.citations import build_citations, format_sources_footer
from app.rag.embeddings import get_embedding_provider
from app.rag.types import EmbeddingProvider
from app.schemas.chat import ChatReply, ConversationNotFoundError, HandoffInfo, SourceRef
from app.services.attempt_limiter import AttemptLimiter
from app.services.attempt_limiter_redis import RedisAttemptLimiter
from app.services.model_gateway import GuardedModelProvider, build_model_provider
from app.services.privacy import (
    PrivacyBoundaryError,
    looks_restricted,
    public_persona_values,
    redact_user_text,
    scan_outbound,
)
from app.services.privacy_vault import TOKEN_RE, PrivacyVault
from app.services.vault_store import VaultStore
from app.tools.context import ToolCallRecord, ToolContext
from app.tools.handoff import request_human_handoff
from emporio_agent.privacy import redact_for_log

ModelProviderFactory = Callable[[Settings, Iterable[str]], ModelProvider]
logger = structlog.get_logger(__name__)

# Stored instead of the redacted text when a message still looked restricted, so it can
# never reach the external model through later history either.
RESTRICTED_PLACEHOLDER = "[mensagem omitida: continha dados pessoais]"
_FAILURE_ERRORS = frozenset({"unavailable", "internal_error"})


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


def _caused_by_privacy_guard(exc: BaseException) -> bool:
    """The SDK wraps tool exceptions (UserError); walk the cause/context chain."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, PrivacyBoundaryError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


class _Handoffs:
    """At most one open HandoffRequest per conversation; tracks this turn's request."""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self.turn_reason: str | None = None

    async def request(self, reason: str) -> None:
        if self.ctx.handoff_id is None:
            try:
                await request_human_handoff(
                    self.ctx, reason, "Solicitação encaminhada para análise humana."
                )
            except Exception:
                # No success claim when even the handoff system is unavailable.
                self.ctx.tool_calls.append(
                    ToolCallRecord(
                        tool_name="request_human_handoff",
                        arguments_redacted={"reason": reason},
                        status="error",
                        error_code="unavailable",
                        result_summary={},
                        latency_ms=0,
                    )
                )
        if self.ctx.handoff_id is not None and self.turn_reason is None:
            self.turn_reason = reason

    def note_model_requests(self) -> None:
        for record in self.ctx.tool_calls:
            if record.tool_name == "request_human_handoff" and record.status == "success":
                reason = record.arguments_redacted.get("reason")
                if self.turn_reason is None and self.ctx.handoff_id is not None:
                    self.turn_reason = (
                        reason
                        if isinstance(reason, str) and reason in HANDOFF_REASONS
                        else self.ctx.handoff_reason or "customer_request"
                    )


def _default_limiter(settings: Settings) -> AttemptLimiter:
    return RedisAttemptLimiter.from_settings(settings)


class ChatService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        vault_store: VaultStore,
        model_provider_factory: ModelProviderFactory | None = None,
        limiter: AttemptLimiter | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.vault_store = vault_store
        self.model_provider_factory = model_provider_factory or build_model_provider
        self.limiter = limiter if limiter is not None else _default_limiter(settings)
        self.embedder = _LazyEmbeddings(settings)

    async def _conversation(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        channel: str,
        external_user_ref: str | None,
    ) -> Conversation:
        if conversation_id is None:
            conversation = Conversation(
                tenant_id=tenant_id, channel=channel, external_user_ref=external_user_ref
            )
            session.add(conversation)
            await session.flush()
            return conversation
        found = await session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
            )
        )
        # A conversation id is not a bearer secret: it is bound to channel and end user.
        if (
            found is None
            or found.channel != channel
            or (
                (found.external_user_ref or external_user_ref)
                and found.external_user_ref != external_user_ref
            )
        ):
            raise ConversationNotFoundError("Conversation not found")
        return found

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
        conversation = await self._conversation(
            session, tenant_id, conversation_id, channel, external_user_ref
        )
        conversation_id = conversation.id
        agent_settings = await session.scalar(
            select(AgentSettings).where(AgentSettings.tenant_id == tenant_id)
        )
        if agent_settings is None:
            agent_settings = AgentSettings(
                tenant_id=tenant_id,
                assistant_name="Assistente",
                public_contact_values=[],
            )
        customers = (
            await session.execute(
                select(CommerceCustomer.name, CommerceCustomer.email, CommerceCustomer.phone).where(
                    CommerceCustomer.tenant_id == tenant_id
                )
            )
        ).all()
        known_names = [row.name for row in customers if row.name]
        known_values = known_names + [
            value for row in customers for value in (row.email, row.phone) if value
        ]
        open_handoff = await session.scalar(
            select(HandoffRequest)
            .where(
                HandoffRequest.tenant_id == tenant_id,
                HandoffRequest.conversation_id == conversation_id,
                HandoffRequest.status == "open",
            )
            .order_by(HandoffRequest.created_at.desc())
            .limit(1)
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
        # Only values the customer typed in THIS turn may be echoed back (revealed).
        turn_tokens = {m.group() for m in TOKEN_RE.finditer(redacted)}
        restricted = looks_restricted(redacted)
        user_message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role="user",
            content=text,
            content_redacted=RESTRICTED_PLACEHOLDER if restricted else redacted,
            correlation_id=safe_correlation,
        )
        session.add(user_message)
        # Tools use independent sessions, so the conversation FK must be committed first.
        await session.commit()
        if agent_settings.welcome_message:
            # Serialize first-contact decisions across API workers without locking the
            # conversation row needed by independently committed handoff tools.
            lock_key = int.from_bytes(conversation_id.bytes[:8], "big", signed=True)
            await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        first_contact = not await session.scalar(
            select(Message.id)
            .where(
                Message.tenant_id == tenant_id,
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .limit(1)
        )
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
            if message.role == "user":
                content = redact_user_text(content, vault, known_restricted_values=known_names)
                if looks_restricted(content):
                    content = RESTRICTED_PLACEHOLDER
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
            limiter=self.limiter,
            # Per-actor lockout only for a real end-user ref; tools fall back to the
            # conversation (a tenant-wide bucket would let one caller lock out everyone).
            actor_ref=external_user_ref,
        )
        if open_handoff is not None:
            # Reused by the handoff tool instead of opening another request.
            ctx.handoff_id, ctx.handoff_reason = open_handoff.id, open_handoff.reason
        handoffs = _Handoffs(ctx)
        public_values = agent_settings.public_contact_values or []
        # The assistant may introduce itself by its configured (public) name.
        store_name = await session.scalar(select(Tenant.name).where(Tenant.id == tenant_id))
        scan_allowlist = [
            *public_values,
            *public_persona_values(agent_settings.assistant_name, store_name or ""),
        ]
        decision = check_input(text)
        welcome: str | None = None
        welcome_only = False
        configured_welcome = (agent_settings.welcome_message or "").strip()
        if (
            first_contact
            and configured_welcome
            and vault_ok
            and not (decision.refusal or decision.reason or restricted)
        ):
            # Operator text skips factual/model-output checks, but is never allowed
            # to contain customer PII or vault references (including on later turns).
            try:
                scan_outbound(
                    configured_welcome,
                    allowlist=scan_allowlist,
                    known_restricted_values=known_values,
                )
                if not TOKEN_RE.search(configured_welcome):
                    welcome = configured_welcome
            except PrivacyBoundaryError:
                pass
        answer = SAFE_FALLBACK
        failure_reason: str | None = None
        model_ran = False
        turn_instructions: str | None = None
        provider: ModelProvider | None = None
        if decision.refusal:
            answer = INJECTION_REFUSAL
        elif restricted:
            # Fail closed: residual address/identity/CPF cues never reach the model.
            answer = PRIVACY_NOTICE
            await handoffs.request("privacy_guard")
        elif decision.reason:
            await handoffs.request(decision.reason)
            answer = HANDOFF_ACK
        elif not vault_ok:
            failure_reason = "privacy_guard"
        elif welcome and is_greeting_only(text):
            answer = welcome
            welcome_only = True
        else:
            try:
                async with asyncio.timeout(self.settings.turn_timeout_seconds):
                    provider = self.model_provider_factory(self.settings, scan_allowlist)
                    # Injected test/custom providers cannot bypass the boundary either.
                    guarded = GuardedModelProvider(
                        provider,
                        scan_allowlist,
                        known_values,
                        timeout_seconds=self.settings.model_timeout_seconds,
                    )
                    welcome_already_sent = bool(welcome) and not welcome_only
                    # Build once: model and output_violation must see the identical string.
                    turn_instructions = build_instructions(
                        agent_settings,
                        welcome_already_sent=welcome_already_sent,
                    )
                    agent = build_customer_service_agent(
                        agent_settings,
                        instructions=turn_instructions,
                    )
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
                            model_settings=ModelSettings(
                                store=False,
                                parallel_tool_calls=False,
                                reasoning=Reasoning(effort=self.settings.openai_reasoning_effort)
                                if self.settings.llm_provider == "openai"
                                and self.settings.openai_reasoning_effort
                                else None,
                            ),
                        ),
                    )
                    answer = str(result.final_output)
                    model_ran = True
            except Exception as exc:
                # Includes SDK MaxTurnsExceeded, provider/tool errors and TimeoutError.
                failure_reason = (
                    "privacy_guard" if _caused_by_privacy_guard(exc) else "tool_failure"
                )
            finally:
                if provider is not None:
                    try:
                        await provider.aclose()
                    except Exception:
                        logger.warning("model.cleanup_failed", correlation_id=safe_correlation)
        handoffs.note_model_requests()
        citations = build_citations(ctx.retrieved_chunks)
        labels = {citation.label for citation in citations}
        cited = set(re.findall(r"\[(S\d+)\]", answer))
        selected = [citation for citation in citations if citation.label in cited]
        authorized_tokens = verified_lookup_tokens(ctx.tool_calls)
        tool_grounded = False
        if model_ran:
            # Recoverable tool errors (invalid_input/not_found/not_authorized) keep the answer.
            for record in ctx.tool_calls:
                if record.result_summary.get("locked") is True:
                    failure_reason = "privacy_guard"
                elif (
                    failure_reason is None
                    and record.status == "error"
                    and record.error_code in _FAILURE_ERRORS
                ):
                    failure_reason = "tool_failure"
            cited_chunks = {c.label: c for c in citations}
            documents = [
                chunk.content
                for chunk in ctx.retrieved_chunks
                if any(
                    cited_chunks[label].chunk_id == chunk.chunk_id
                    for label in cited
                    if label in cited_chunks
                )
            ]
            violation = output_violation(
                answer,
                records=ctx.tool_calls,
                labels=labels,
                instructions=turn_instructions or build_instructions(agent_settings),
                policy_text=POLICY_TEXT,
                documents=documents,
                current_message=redacted,
            )
            if violation is not None:
                logger.info("chat.output_rejected", rule=violation, correlation_id=safe_correlation)
                failure_reason = failure_reason or "low_confidence"
            # Raw personal data invented/copied by a model must never enter future history.
            try:
                scan_outbound(
                    answer, allowlist=scan_allowlist, known_restricted_values=known_values
                )
            except PrivacyBoundaryError:
                failure_reason = "privacy_guard"
            if any(
                m.group(1) in {"TRACKING", "DATE"} and m.group() not in authorized_tokens
                for m in TOKEN_RE.finditer(answer)
            ):
                failure_reason = "privacy_guard"
            tool_grounded = answer_uses_tool_facts(answer, ctx.tool_calls)
        if failure_reason:
            answer, selected, tool_grounded = SAFE_FALLBACK, [], False
            await handoffs.request(failure_reason)
        elif model_ran and not selected and not tool_grounded:
            tagline = (agent_settings.extra or {}).get("tagline", "")
            persona = (
                store_name or "",
                agent_settings.assistant_name,
                agent_settings.business_description or "",
                tagline if isinstance(tagline, str) else "",
            )
            if not is_out_of_scope_refusal(answer) and (
                admits_no_information(answer) or has_factual_claim(answer, persona=persona)
            ):
                answer = NO_INFORMATION
                await handoffs.request("no_information")
            # Otherwise: greeting/thanks/clarification question without factual claims.
        if (
            model_ran
            and failure_reason is None
            and handoffs.turn_reason is None
            and discusses_sensitive_topic(answer, cited=bool(selected))
        ):
            await handoffs.request("sensitive_topic")
        grounded = model_ran and failure_reason is None and bool(selected or tool_grounded)
        try:
            await self.vault_store.save(tenant_id, conversation_id, vault)
        except Exception:
            answer, selected, grounded = SAFE_FALLBACK, [], False
            welcome, welcome_only = None, False
            await handoffs.request("privacy_guard")
        footer = format_sources_footer(selected)
        # Citation metadata is untrusted too; it must not smuggle vault-token reveals.
        if any(
            m.group(1) in {"TRACKING", "DATE"} and m.group() not in authorized_tokens
            for m in TOKEN_RE.finditer(footer)
        ):
            answer, selected, grounded, footer = SAFE_FALLBACK, [], False, ""
            welcome, welcome_only = None, False
            await handoffs.request("privacy_guard")
        handoff_this_turn = handoffs.turn_reason is not None and ctx.handoff_id is not None
        if decision.refusal or handoff_this_turn or failure_reason:
            welcome = None
            welcome_only = False
        if handoff_this_turn:
            answer += "\nPassei sua solicitação pra um atendente te ajudar."
            conversation.status = "handoff"
        if footer:
            answer += "\n\n" + footer
        # Reveal only what the customer typed this turn or a verified lookup produced now;
        # tokens from earlier turns (or other callers) are never turned back into values.
        revealable = turn_tokens | authorized_tokens
        answer = TOKEN_RE.sub(
            lambda m: m.group() if m.group() in revealable else HIDDEN_VALUE, answer
        )
        revealed = vault.reveal(answer)
        latency = int((time.monotonic() - started) * 1000)
        if welcome and not welcome_only:
            session.add(
                Message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=welcome,
                    content_redacted=welcome,
                    correlation_id=safe_correlation,
                    meta={"kind": "welcome", "grounded": False, "handoff": False},
                )
            )
            await session.flush()
        assistant = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role="assistant",
            content=revealed,
            content_redacted=answer,
            correlation_id=safe_correlation,
            meta={
                **({"kind": "welcome"} if welcome_only else {}),
                "grounded": grounded,
                "handoff": handoff_this_turn,
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
            welcome_message=welcome if not welcome_only else None,
            sources=[SourceRef(**citation.model_dump()) for citation in selected],
            handoff=HandoffInfo(
                handoff_id=ctx.handoff_id, reason=handoffs.turn_reason or "tool_failure"
            )
            if handoff_this_turn and ctx.handoff_id is not None
            else None,
            grounded=grounded,
            tools_used=[record.tool_name for record in ctx.tool_calls],
            tool_errors=sum(record.status == "error" for record in ctx.tool_calls),
            retrieved_chunks=len(ctx.retrieved_chunks),
            latency_ms=latency,
        )
