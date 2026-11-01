"""Commerce backend contract. Implementations: ``local_commerce.LocalCommerceBackend``
(Postgres tables seeded from the tenant's CSV export). A real ERP/CRM adapter implements
the same protocol. Every backend instance is bound to exactly one tenant at construction;
methods never accept a tenant id from the caller/model."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel

ProductStatus = Literal["active", "discontinued", "coming_soon"]


class ProductQuery(BaseModel):
    text: str | None = None  # matched against name/description/category (case/accent-insensitive)
    category: str | None = None  # category name (case/accent-insensitive)
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    in_stock_only: bool = False
    limit: int = 10


class Promotion(BaseModel):
    description: str
    discount_percent: Decimal
    promotional_price: Decimal


class ProductSummary(BaseModel):
    product_id: int
    name: str
    category: str
    price: Decimal  # list price
    currency: str
    status: ProductStatus
    available: bool  # status == active and stock_quantity > 0
    active_promotion: Promotion | None = None


class ProductDetails(ProductSummary):
    description: str
    specs: dict[str, Any]
    stock_quantity: int


class StockInfo(BaseModel):
    product_id: int
    name: str
    status: ProductStatus
    stock_quantity: int
    available: bool


class OrderRecord(BaseModel):
    """RESTRICTED. Lives only inside the trusted boundary; never returned to the model."""

    order_id: int
    status: str
    order_date: date
    tracking_code: str | None
    estimated_delivery: date | None
    payment_method: str
    total: Decimal
    customer_phone: str
    customer_email: str


class BackendUnavailableError(RuntimeError):
    """Raised by a backend on timeout/outage; tools convert it to ToolError('unavailable')."""


class CommerceBackend(Protocol):
    tenant_id: uuid.UUID

    async def search_products(self, query: ProductQuery) -> list[ProductSummary]: ...

    async def get_product(self, product_id: int) -> ProductDetails | None: ...

    async def get_stock(self, product_id: int) -> StockInfo | None: ...

    async def get_order(self, order_id: int) -> OrderRecord | None: ...
