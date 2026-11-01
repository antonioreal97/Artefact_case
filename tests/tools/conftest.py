"""Tiny per-tenant commerce dataset (NOT the CSV seed) + ToolContext builder."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.base import CommerceBackend
from app.integrations.local_commerce import LocalCommerceBackend
from app.models import (
    CommerceCategory,
    CommerceCustomer,
    CommerceOrder,
    CommerceProduct,
    CommercePromotion,
    Conversation,
)
from app.services.privacy_vault import PrivacyVault
from app.tools.context import ToolContext

try:  # Task C's deterministic provider; fall back to a local fake if absent.
    from app.rag.embeddings import HashEmbeddingProvider as _Embedder
except ImportError:  # pragma: no cover

    class _Embedder:  # type: ignore[no-redef]
        model_name = "fake"
        dimensions = 8

        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 8 for _ in texts]

        async def embed_query(self, text: str) -> list[float]:
            return [0.0] * 8


# Restricted values of tenant A (used by the tests to assert they never leak).
A_EMAIL = "Lucas.Mendes@jmail.com"
A_PHONE = "(67) 99812-3456"
A_NAME = "Lucas Mendes da Silva"
A_TRACKING = "BRAB1234567BR"
A_DELIVERY = date(2026, 11, 5)
B_EMAIL = "cliente@lojasintetica.test"


@dataclass
class Seed:
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID
    conversation_a: uuid.UUID
    conversation_b: uuid.UUID


def _catalog_a(t: uuid.UUID) -> list[object]:
    return [
        CommerceCategory(tenant_id=t, external_id=1, name="Guitarras", description="Elétricas"),
        CommerceCategory(tenant_id=t, external_id=5, name="Violões", description="Acústicos"),
        CommerceProduct(
            tenant_id=t,
            external_id=1,
            category_external_id=1,
            name="Fender Stratocaster Player",
            description="Guitarra elétrica clássica",
            price=Decimal("7999.00"),
            stock_quantity=3,
            status="active",
            specs={"body": "Alder"},
        ),
        CommerceProduct(
            tenant_id=t,
            external_id=2,
            category_external_id=1,
            name="Gibson Les Paul Studio",
            description="Guitarra de mogno",
            price=Decimal("12999.90"),
            stock_quantity=0,
            status="active",
            specs={},
        ),
        CommerceProduct(
            tenant_id=t,
            external_id=3,
            category_external_id=1,
            name="Ibanez RG Antiga",
            description="Modelo fora de linha",
            price=Decimal("2500.00"),
            stock_quantity=5,
            status="discontinued",
            specs={},
        ),
        CommerceProduct(
            tenant_id=t,
            external_id=4,
            category_external_id=5,
            name="Yamaha C40 Nylon",
            description="Violão clássico para iniciantes",
            price=Decimal("599.90"),
            stock_quantity=12,
            status="active",
            specs={"strings": "nylon"},
        ),
        CommerceProduct(
            tenant_id=t,
            external_id=5,
            category_external_id=5,
            name="Tagima Lançamento Aço",
            description="Violão folk em pré-venda",
            price=Decimal("999.99"),
            stock_quantity=0,
            status="coming_soon",
            specs={},
        ),
        # Product 1: 12.5% -> 6999.125 -> HALF_UP 6999.13; inactive 20% must be ignored.
        CommercePromotion(
            tenant_id=t,
            external_id=1,
            product_external_id=1,
            discount_percent=Decimal("12.5"),
            description="Semana do Rock",
            is_active=True,
        ),
        CommercePromotion(
            tenant_id=t,
            external_id=2,
            product_external_id=1,
            discount_percent=Decimal("20"),
            description="Encerrada",
            is_active=False,
        ),
        # Product 4: two active -> highest (15%) wins: 599.90 * 0.85 = 509.915 -> 509.92.
        CommercePromotion(
            tenant_id=t,
            external_id=3,
            product_external_id=4,
            discount_percent=Decimal("10"),
            description="Black Friday",
            is_active=True,
        ),
        CommercePromotion(
            tenant_id=t,
            external_id=4,
            product_external_id=4,
            discount_percent=Decimal("15"),
            description="Queima de Estoque",
            is_active=True,
        ),
        CommerceCustomer(
            tenant_id=t,
            external_id=1,
            name=A_NAME,
            phone=A_PHONE,
            email=A_EMAIL,
            city="Campo Grande",
        ),
        CommerceOrder(
            tenant_id=t,
            external_id=101,
            customer_external_id=1,
            order_date=date(2026, 10, 20),
            status="shipped",
            total=Decimal("7999.00"),
            payment_method="pix",
            tracking_code=A_TRACKING,
            estimated_delivery=A_DELIVERY,
        ),
        CommerceOrder(
            tenant_id=t,
            external_id=102,
            customer_external_id=1,
            order_date=date(2026, 10, 28),
            status="pending",
            total=Decimal("599.90"),
            payment_method="credit_6x",
            tracking_code=None,
            estimated_delivery=None,
        ),
    ]


def _catalog_b(t: uuid.UUID) -> list[object]:
    # Same external ids as tenant A on purpose (product 1, customer 1, order 101).
    return [
        CommerceCategory(tenant_id=t, external_id=1, name="Teclados", description=""),
        CommerceProduct(
            tenant_id=t,
            external_id=1,
            category_external_id=1,
            name="Teclado Sintético X",
            description="Teclado de testes",
            price=Decimal("100.00"),
            stock_quantity=1,
            status="active",
            specs={},
        ),
        CommerceCustomer(
            tenant_id=t,
            external_id=1,
            name="Cliente Sintético",
            phone="(11) 91234-5678",
            email=B_EMAIL,
            city=None,
        ),
        CommerceOrder(
            tenant_id=t,
            external_id=101,
            customer_external_id=1,
            order_date=date(2026, 10, 1),
            status="delivered",
            total=Decimal("100.00"),
            payment_method="pix",
            tracking_code="BRZZ0000001BR",
            estimated_delivery=date(2026, 10, 9),
        ),
    ]


@pytest.fixture
async def seed(
    session_factory: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> Seed:
    a, b = two_tenants
    conv_a = Conversation(tenant_id=a, channel="api")
    conv_b = Conversation(tenant_id=b, channel="api")
    async with session_factory() as s:
        s.add_all([*_catalog_a(a), *_catalog_b(b), conv_a, conv_b])
        await s.commit()
    return Seed(a, b, conv_a.id, conv_b.id)


def make_ctx(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    commerce: CommerceBackend | None = None,
) -> ToolContext:
    return ToolContext(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        correlation_id=f"test-{uuid.uuid4().hex[:8]}",
        session_factory=session_factory,
        commerce=commerce or LocalCommerceBackend(session_factory, tenant_id),
        embedder=_Embedder(),
        vault=PrivacyVault(),
    )


@pytest.fixture
def ctx_factory(
    session_factory: async_sessionmaker[AsyncSession], seed: Seed
) -> Callable[..., ToolContext]:
    """``ctx_factory(commerce=None, tenant="a")`` -> ToolContext for the seeded tenant."""

    def build(commerce: CommerceBackend | None = None, tenant: str = "a") -> ToolContext:
        if tenant == "a":
            return make_ctx(session_factory, seed.tenant_a, seed.conversation_a, commerce)
        return make_ctx(session_factory, seed.tenant_b, seed.conversation_b, commerce)

    return build


@pytest.fixture
def ctx_a(session_factory: async_sessionmaker[AsyncSession], seed: Seed) -> ToolContext:
    return make_ctx(session_factory, seed.tenant_a, seed.conversation_a)


@pytest.fixture
def ctx_b(session_factory: async_sessionmaker[AsyncSession], seed: Seed) -> ToolContext:
    return make_ctx(session_factory, seed.tenant_b, seed.conversation_b)
