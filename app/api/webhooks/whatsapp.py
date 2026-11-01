"""WhatsApp Cloud API webhook adapter.

ADAPTER NOT VALIDATED AGAINST THE LIVE META API. It follows Meta's documented webhook
contract (verification handshake + ``X-Hub-Signature-256`` + ``entry[].changes[].value``
payloads) and is covered only by tests with fakes.

Flow (POST): verify HMAC-SHA256 of the raw body with ``WHATSAPP_APP_SECRET`` (401 on
mismatch) -> for every inbound *text* message: resolve the tenant from
``TenantChannel(channel="whatsapp", external_id=metadata.phone_number_id)``, dedupe by
message id (Redis ``SET NX EX``; if Redis is down we process anyway), derive
``external_user_ref = sha256(pepper + wa_id)`` (the raw phone number is never stored or
logged), reuse the user's latest non-closed WhatsApp conversation, run ``ChatService`` and
send the reply with ``MetaWhatsAppClient``. After the signature check the endpoint always
answers 200 so Meta does not start a retry storm; per-message failures are logged/metered.

Messages are processed inline (simple, testable); a high-volume deployment should move the
per-message work to the arq queue and answer Meta immediately.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    ChatHandler,
    ChatServiceDep,
    RedisDep,
    SessionDep,
    SettingsDep,
    WhatsAppClientDep,
)
from app.api.routes.chat import record_turn_metrics
from app.core.config import Settings
from app.core.logging import bind_conversation_id, bind_tenant_id, get_logger
from app.core.metrics import MODEL_ERRORS, WHATSAPP_MESSAGES
from app.integrations.meta_whatsapp import MetaWhatsAppClient
from app.models import Conversation, Tenant, TenantChannel

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
log = get_logger(__name__)

DEDUPE_TTL_SECONDS = 24 * 60 * 60
MAX_INBOUND_TEXT = 4000  # same limit as the HTTP chat API


@dataclass(frozen=True)
class InboundText:
    phone_number_id: str
    message_id: str
    wa_id: str
    text: str


def external_user_ref(settings: Settings, wa_id: str) -> str:
    """Stable, non-reversible (peppered) reference for a WhatsApp user."""
    pepper = settings.api_key_pepper.get_secret_value()
    return hashlib.sha256((pepper + wa_id).encode("utf-8")).hexdigest()


def verify_signature(secret: str, raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_text_messages(payload: Any) -> list[InboundText]:
    """Extract inbound text messages; anything malformed or non-text is skipped."""
    found: list[InboundText] = []
    for entry in _dicts(_dict(payload).get("entry")):
        for change in _dicts(entry.get("changes")):
            value = _dict(change.get("value"))
            phone_number_id = _dict(value.get("metadata")).get("phone_number_id")
            for message in _dicts(value.get("messages")):
                if message.get("type") != "text":
                    continue
                fields = (
                    phone_number_id,
                    message.get("id"),
                    message.get("from"),
                    _dict(message.get("text")).get("body"),
                )
                if all(isinstance(f, str) and f for f in fields):
                    pid, msg_id, wa_id, body = (str(f) for f in fields)
                    found.append(InboundText(pid, msg_id, wa_id, body.strip()))
    return found


@router.get("/whatsapp", response_class=PlainTextResponse)
async def verify_webhook(
    settings: SettingsDep,
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge", max_length=256)] = None,
) -> PlainTextResponse:
    """Meta subscription handshake:
    `GET /v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=…&hub.challenge=1158201444`
    → `200 text/plain 1158201444`; wrong token → 403; not configured → 503."""
    expected = settings.whatsapp_verify_token
    if expected is None or not expected.get_secret_value():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="WhatsApp not configured")
    token_ok = hmac.compare_digest(
        (hub_verify_token or "").encode("utf-8"), expected.get_secret_value().encode("utf-8")
    )
    if hub_mode != "subscribe" or not token_ok or not hub_challenge:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Verification failed")
    return PlainTextResponse(hub_challenge)


@router.post("/whatsapp")
async def receive_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    chat: ChatServiceDep,
    client: WhatsAppClientDep,
    redis: RedisDep,
) -> dict[str, int | str]:
    """Inbound notifications from Meta (signed with `X-Hub-Signature-256: sha256=<hex>`).

    Example body: `{"object": "whatsapp_business_account", "entry": [{"changes": [{"value":
    {"metadata": {"phone_number_id": "1234567890"}, "messages": [{"id": "wamid.X",
    "from": "5511…", "type": "text", "text": {"body": "Vocês abrem domingo?"}}]}}]}]}`

    Response: `{"status": "ok", "processed": 1}` (always 200 once the signature is valid)."""
    secret = settings.whatsapp_app_secret
    if secret is None or not secret.get_secret_value():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="WhatsApp not configured")
    raw = await request.body()
    if not verify_signature(
        secret.get_secret_value(), raw, request.headers.get("X-Hub-Signature-256")
    ):
        log.warning("whatsapp.bad_signature")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        log.warning("whatsapp.malformed_payload")
        return {"status": "ignored", "processed": 0}

    processed = 0
    for inbound in parse_text_messages(payload):
        try:
            if await _handle_message(
                inbound, session, settings, chat, client, redis, request.state.correlation_id
            ):
                processed += 1
        except Exception as exc:
            await session.rollback()
            WHATSAPP_MESSAGES.inc(outcome="error")
            MODEL_ERRORS.inc(error_type=type(exc).__name__)
            log.error("whatsapp.message_failed", exc_info=exc)
        finally:
            bind_tenant_id(None)
            bind_conversation_id(None)
    return {"status": "ok", "processed": processed}


async def _resolve_tenant(session: AsyncSession, phone_number_id: str) -> uuid.UUID | None:
    tenant_id: uuid.UUID | None = await session.scalar(
        select(TenantChannel.tenant_id)
        .join(Tenant, Tenant.id == TenantChannel.tenant_id)
        .where(
            TenantChannel.channel == "whatsapp",
            TenantChannel.external_id == phone_number_id,
            Tenant.status == "active",
        )
    )
    return tenant_id


async def _first_delivery(redis: Any, tenant_id: uuid.UUID, message_id: str) -> bool:
    """True if this message id was not seen before. Degrades to True if Redis fails."""
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:32]
    try:
        created = await redis.set(
            f"wa:dedupe:{tenant_id}:{digest}", "1", nx=True, ex=DEDUPE_TTL_SECONDS
        )
    except Exception as exc:
        log.warning("whatsapp.dedupe_unavailable", error_type=type(exc).__name__)
        return True
    return bool(created)


async def _handle_message(
    inbound: InboundText,
    session: AsyncSession,
    settings: Settings,
    chat: ChatHandler,
    client: MetaWhatsAppClient,
    redis: Any,
    correlation_id: str,
) -> bool:
    tenant_id = await _resolve_tenant(session, inbound.phone_number_id)
    if tenant_id is None:
        WHATSAPP_MESSAGES.inc(outcome="unknown_channel")
        log.warning("whatsapp.unknown_channel")
        return False
    bind_tenant_id(tenant_id)
    if not await _first_delivery(redis, tenant_id, inbound.message_id):
        WHATSAPP_MESSAGES.inc(outcome="duplicate")
        log.info("whatsapp.duplicate_ignored")
        return False
    if not inbound.text:
        WHATSAPP_MESSAGES.inc(outcome="empty")
        return False

    user_ref = external_user_ref(settings, inbound.wa_id)
    conversation_id = await session.scalar(
        select(Conversation.id)
        .where(
            Conversation.tenant_id == tenant_id,
            Conversation.channel == "whatsapp",
            Conversation.external_user_ref == user_ref,
            Conversation.status != "closed",
        )
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )
    if conversation_id is not None:
        bind_conversation_id(conversation_id)
    reply = await chat.handle_message(
        session,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        text=inbound.text[:MAX_INBOUND_TEXT],
        channel="whatsapp",
        external_user_ref=user_ref,
        correlation_id=correlation_id,
    )
    bind_conversation_id(reply.conversation_id)
    record_turn_metrics(reply, "whatsapp")
    sent = await client.send_text(
        phone_number_id=inbound.phone_number_id, to=inbound.wa_id, text=reply.reply
    )
    WHATSAPP_MESSAGES.inc(outcome="replied" if sent else "reply_not_sent")
    log.info("whatsapp.message_processed", message_id=str(reply.message_id), reply_sent=sent)
    return True
