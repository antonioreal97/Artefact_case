"""Adversarial harness: the REAL HTTP app with the REAL ChatService (deterministic fake LLM
via the per-turn model factory, hash embeddings, InMemoryVaultStore) and REAL inline
ingestion against the throw-away test database. Two tenants, each with its own API key,
catalog, customer, order and documents (one private, one byte-identical in both)."""

from __future__ import annotations

import io
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import pytest
from adv_support import (
    A_DATA,
    B_DATA,
    MAX_UPLOAD,
    SHARED_DOC,
    ModelControl,
    TenantData,
    TenantWorld,
    World,
)
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import dependencies as deps
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.logging import configure_logging
from app.core.security import generate_api_key, hash_api_key
from app.main import create_app
from app.models import (
    AgentSettings,
    ApiKey,
    CommerceCategory,
    CommerceCustomer,
    CommerceOrder,
    CommerceProduct,
    Tenant,
)
from app.rag.embeddings import HashEmbeddingProvider
from app.services.attempt_limiter import InMemoryAttemptLimiter
from app.services.chat import ChatService
from app.services.ingestion_jobs import ingest_document_version
from app.services.model_gateway import GuardedModelProvider
from app.services.vault_store import InMemoryVaultStore

ALL_SCOPES = list(deps.SCOPES)


@pytest.fixture
def adv_settings() -> Settings:
    return Settings(
        app_env="test",
        llm_provider="fake",
        embedding_provider="hash",
        api_key_pepper=SecretStr("adv-pepper"),
        max_upload_bytes=MAX_UPLOAD,
        whatsapp_verify_token=SecretStr("verify-me"),
        whatsapp_app_secret=SecretStr("app-secret"),
        whatsapp_access_token=None,
    )


@pytest.fixture
def model_control() -> ModelControl:
    return ModelControl()


@pytest.fixture
def chat_service(
    session_factory: async_sessionmaker[AsyncSession],
    adv_settings: Settings,
    model_control: ModelControl,
) -> ChatService:
    return ChatService(
        session_factory,
        adv_settings,
        InMemoryVaultStore(),
        lambda settings, allowlist: GuardedModelProvider(model_control.new(), allowlist),
        limiter=InMemoryAttemptLimiter(),  # same semantics as the Redis limiter, per test
    )


class _NullRedis:
    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> Any:
        return True

    async def ping(self) -> bool:
        return True


@dataclass
class _NullWhatsApp:
    sent: list[dict[str, str]] = field(default_factory=list)

    async def send_text(self, *, phone_number_id: str, to: str, text: str) -> bool:
        self.sent.append({"phone_number_id": phone_number_id, "to": to, "text": text})
        return True


@pytest.fixture
def app(
    adv_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    chat_service: ChatService,
) -> Any:
    application = create_app(adv_settings)
    embedder = HashEmbeddingProvider(adv_settings)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    async def _inline_ingestion(tenant_id: uuid.UUID, version_id: uuid.UUID) -> None:
        # The real worker job, bound to the test database and hash embeddings.
        await ingest_document_version(
            {"session_factory": session_factory, "embedder": embedder}, tenant_id, version_id
        )

    whatsapp = _NullWhatsApp()
    auth_limiter = InMemoryAttemptLimiter()
    application.dependency_overrides.update(
        {
            get_session: _session,
            get_settings: lambda: adv_settings,
            deps.get_chat_service: lambda: chat_service,
            deps.get_ingestion_enqueuer: lambda: _inline_ingestion,
            deps.get_redis: lambda: _NullRedis(),
            deps.get_whatsapp_client: lambda: whatsapp,
            deps.get_attempt_limiter: lambda: auth_limiter,
        }
    )
    return application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@dataclass
class LogCapture:
    stream: io.StringIO
    caplog: pytest.LogCaptureFixture

    def text(self) -> str:
        return self.stream.getvalue() + "\n" + self.caplog.text


@pytest.fixture
def logs(
    app: Any, adv_settings: Settings, caplog: pytest.LogCaptureFixture
) -> Iterator[LogCapture]:
    """structlog JSON output + every stdlib record (DEBUG and up, SDK loggers included)."""
    stream = io.StringIO()
    configure_logging(adv_settings, stream=stream)
    caplog.set_level(logging.DEBUG)
    yield LogCapture(stream, caplog)
    configure_logging(adv_settings)


async def _make_key(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, tenant_id: uuid.UUID
) -> str:
    full_key, prefix = generate_api_key()
    async with session_factory() as s:
        s.add(
            ApiKey(
                tenant_id=tenant_id,
                name="adversarial",
                key_prefix=prefix,
                key_hash=hash_api_key(full_key, settings),
                scopes=ALL_SCOPES,
            )
        )
        await s.commit()
    return full_key


async def _seed_commerce(
    session_factory: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, data: TenantData
) -> None:
    price = Decimal(data.product_price_text.replace(".", "").replace(",", "."))
    async with session_factory() as s:
        s.add_all(
            [
                CommerceCategory(
                    tenant_id=tenant_id, external_id=1, name="Instrumentos", description=""
                ),
                # Same external product id (1) in both tenants on purpose.
                CommerceProduct(
                    tenant_id=tenant_id,
                    external_id=1,
                    category_external_id=1,
                    name=data.product_name,
                    description=f"Produto exclusivo {data.marker}",
                    price=price,
                    currency="BRL",
                    stock_quantity=3,
                    status="active",
                    specs={},
                ),
                CommerceCustomer(
                    tenant_id=tenant_id,
                    external_id=1,
                    name=data.customer_name,
                    email=data.email,
                    phone=data.phone,
                ),
                CommerceOrder(
                    tenant_id=tenant_id,
                    external_id=data.order_id,
                    customer_external_id=1,
                    order_date=data.delivery.replace(day=1),
                    status="shipped",
                    total=price,
                    payment_method="pix",
                    tracking_code=data.tracking,
                    estimated_delivery=data.delivery,
                ),
            ]
        )
        agent_settings = await s.scalar(
            select(AgentSettings).where(AgentSettings.tenant_id == tenant_id)
        )
        assert agent_settings is not None
        agent_settings.retrieval_min_similarity = 0.1
        await s.commit()


async def _text_doc(
    client: httpx.AsyncClient, key: str, title: str, content: str
) -> tuple[uuid.UUID, uuid.UUID]:
    response = await client.post(
        "/v1/knowledge/documents/text",
        headers={"X-API-Key": key},
        json={"title": title, "content": content, "category": "politicas"},
    )
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["status"] == "completed", receipt
    return uuid.UUID(receipt["document_id"]), uuid.UUID(receipt["version_id"])


@pytest.fixture
async def world(
    client: httpx.AsyncClient,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
    session_factory: async_sessionmaker[AsyncSession],
    adv_settings: Settings,
) -> World:
    tenants: list[TenantWorld] = []
    for tenant_id, data, title in (
        (two_tenants[0], A_DATA, "Manual de garantia Alfa"),
        (two_tenants[1], B_DATA, "Manual de garantia Bravo"),
    ):
        await _seed_commerce(session_factory, tenant_id, data)
        key = await _make_key(session_factory, adv_settings, tenant_id)
        async with session_factory() as s:
            tenant = await s.get(Tenant, tenant_id)
            assert tenant is not None
            slug = tenant.slug
        private_doc, _ = await _text_doc(client, key, title, data.private_doc)
        shared_doc, shared_version = await _text_doc(
            client, key, "Horário de funcionamento", SHARED_DOC
        )
        tenants.append(
            TenantWorld(tenant_id, slug, key, data, private_doc, shared_doc, shared_version)
        )
    return World(tenants[0], tenants[1])
