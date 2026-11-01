"""API test fixtures: app with the test DB session, fake chat/ingestion/Redis/WhatsApp."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import dependencies as deps
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.security import generate_api_key, hash_api_key
from app.main import create_app
from app.models import (
    ApiKey,
    Conversation,
    Document,
    DocumentVersion,
    Message,
    MessageSource,
)
from app.models.base import utcnow
from app.rag.types import IngestionReceipt
from app.schemas.chat import ChatReply, ConversationNotFoundError, HandoffInfo, SourceRef
from app.services.attempt_limiter import InMemoryAttemptLimiter

ALL_SCOPES = list(deps.SCOPES)


@pytest.fixture
def api_settings() -> Settings:
    return Settings(
        app_env="test",
        llm_provider="fake",
        embedding_provider="hash",
        api_key_pepper=SecretStr("test-pepper"),
        max_upload_bytes=4096,
        whatsapp_verify_token=SecretStr("verify-me"),
        whatsapp_app_secret=SecretStr("app-secret"),
        whatsapp_access_token=None,
    )


@dataclass
class FakeChatService:
    """Persists a user + assistant message (and one source) like the real service."""

    session_factory: async_sessionmaker[AsyncSession]
    calls: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None
    handoff_reason: str | None = None

    async def handle_message(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        text: str,
        channel: str = "api",
        external_user_ref: str | None = None,
        correlation_id: str,
    ) -> ChatReply:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "text": text,
                "channel": channel,
                "external_user_ref": external_user_ref,
                "correlation_id": correlation_id,
            }
        )
        if self.error is not None:
            raise self.error
        if conversation_id is None:
            conversation = Conversation(
                tenant_id=tenant_id, channel=channel, external_user_ref=external_user_ref
            )
            session.add(conversation)
            await session.flush()
        else:
            found = await session.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id, Conversation.tenant_id == tenant_id
                )
            )
            if found is None:
                raise ConversationNotFoundError("Conversation not found")
            conversation = found
        user = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role="user",
            content=text,
            content_redacted=text,
            correlation_id=correlation_id,
            meta={},
        )
        answer = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role="assistant",
            content="Trocas em até 7 dias. [S1]",
            content_redacted="Trocas em até 7 dias. [S1]",
            correlation_id=correlation_id,
            meta={"grounded": True, "handoff": False},
        )
        session.add_all([user, answer])
        await session.flush()
        session.add(
            MessageSource(
                tenant_id=tenant_id,
                message_id=answer.id,
                label="S1",
                title="Políticas",
                score=0.9,
                quote="Trocas em até 7 dias.",
            )
        )
        await session.commit()
        doc_id = uuid.uuid4()
        return ChatReply(
            conversation_id=conversation.id,
            user_message_id=user.id,
            message_id=answer.id,
            reply=answer.content,
            sources=[SourceRef(label="S1", document_id=doc_id, title="Políticas", score=0.9)],
            handoff=HandoffInfo(handoff_id=uuid.uuid4(), reason=self.handoff_reason)
            if self.handoff_reason
            else None,
            grounded=True,
            tools_used=["search_knowledge"],
            retrieved_chunks=1,
        )


async def fake_create_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    title: str,
    data: bytes,
    filename: str | None,
    source_type: str,
    category: str | None = None,
    source_url: str | None = None,
    document_id: uuid.UUID | None = None,
) -> IngestionReceipt:
    if document_id is None:
        document = Document(
            tenant_id=tenant_id,
            title=title,
            source_type=source_type,
            filename=filename,
            source_url=source_url,
            category=category,
        )
        session.add(document)
        await session.flush()
        document_id = document.id
        number = 1
    else:
        existing = await session.scalars(
            select(DocumentVersion.version).where(
                DocumentVersion.document_id == document_id, DocumentVersion.tenant_id == tenant_id
            )
        )
        number = max(existing, default=0) + 1
    version = DocumentVersion(
        tenant_id=tenant_id,
        document_id=document_id,
        version=number,
        content_hash=hashlib.sha256(data).hexdigest(),
        mime_type="text/plain",
        byte_size=len(data),
        raw_content=data,
        ingestion_status="pending",
    )
    session.add(version)
    await session.commit()
    return IngestionReceipt(
        document_id=document_id,
        version_id=version.id,
        version=number,
        status="pending",
        duplicate=False,
    )


@dataclass
class FakeEnqueuer:
    calls: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)

    async def __call__(self, tenant_id: uuid.UUID, version_id: uuid.UUID) -> None:
        self.calls.append((tenant_id, version_id))


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.fail = fail

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> Any:
        if self.fail:
            raise ConnectionError("redis down")
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def delete(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis down")
        return 1 if self.data.pop(key, None) is not None else 0

    async def ping(self) -> bool:
        if self.fail:
            raise ConnectionError("redis down")
        return True


@dataclass
class FakeWhatsAppClient:
    sent: list[dict[str, str]] = field(default_factory=list)

    async def send_text(self, *, phone_number_id: str, to: str, text: str) -> bool:
        self.sent.append({"phone_number_id": phone_number_id, "to": to, "text": text})
        return True


@dataclass
class Fakes:
    chat: FakeChatService
    enqueuer: FakeEnqueuer
    redis: FakeRedis
    whatsapp: FakeWhatsAppClient
    limiter: InMemoryAttemptLimiter = field(default_factory=InMemoryAttemptLimiter)


@pytest.fixture
def fakes(session_factory: async_sessionmaker[AsyncSession]) -> Fakes:
    return Fakes(
        FakeChatService(session_factory), FakeEnqueuer(), FakeRedis(), FakeWhatsAppClient()
    )


@pytest.fixture
def app(
    api_settings: Settings, session_factory: async_sessionmaker[AsyncSession], fakes: Fakes
) -> Any:
    application = create_app(api_settings)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    application.dependency_overrides.update(
        {
            get_session: _session,
            get_settings: lambda: api_settings,
            deps.get_chat_service: lambda: fakes.chat,
            deps.get_version_creator: lambda: fake_create_version,
            deps.get_ingestion_enqueuer: lambda: fakes.enqueuer,
            deps.get_redis: lambda: fakes.redis,
            deps.get_whatsapp_client: lambda: fakes.whatsapp,
            deps.get_attempt_limiter: lambda: fakes.limiter,
        }
    )
    return application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


MakeKey = Callable[..., Awaitable[str]]


@pytest.fixture
def make_key(session_factory: async_sessionmaker[AsyncSession], api_settings: Settings) -> MakeKey:
    async def _make(
        tenant_id: uuid.UUID, scopes: list[str] | None = None, revoked: bool = False
    ) -> str:
        full_key, prefix = generate_api_key()
        async with session_factory() as s:
            s.add(
                ApiKey(
                    tenant_id=tenant_id,
                    name="test",
                    key_prefix=prefix,
                    key_hash=hash_api_key(full_key, api_settings),
                    scopes=ALL_SCOPES if scopes is None else scopes,
                    revoked_at=utcnow() if revoked else None,
                )
            )
            await s.commit()
        return full_key

    return _make
