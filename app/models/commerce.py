"""Local commerce store (seeded from the tenant's CSV export). Stands in for an ERP; the
tools reach it only through ``app.integrations.base.CommerceBackend``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Date, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TenantScoped, UUIDPk


class CommerceCategory(UUIDPk, TenantScoped, Base):
    __tablename__ = "commerce_categories"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id"),)

    external_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")


class CommerceProduct(UUIDPk, TenantScoped, Base):
    __tablename__ = "commerce_products"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id"),)

    external_id: Mapped[int] = mapped_column(Integer)
    category_external_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    stock_quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # active | discontinued | coming_soon
    specs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_on: Mapped[date | None] = mapped_column(Date)


class CommercePromotion(UUIDPk, TenantScoped, Base):
    __tablename__ = "commerce_promotions"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id"),)

    external_id: Mapped[int] = mapped_column(Integer)
    product_external_id: Mapped[int] = mapped_column(Integer)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    description: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean)


class CommerceCustomer(UUIDPk, TenantScoped, Base):
    """RESTRICTED personal data. Never serialized to a model or log."""

    __tablename__ = "commerce_customers"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id"),)

    external_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))


class CommerceOrder(UUIDPk, TenantScoped, Base):
    """RESTRICTED. Exposed to the model only as an abstract, tokenized status view."""

    __tablename__ = "commerce_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id"),)

    external_id: Mapped[int] = mapped_column(Integer)
    customer_external_id: Mapped[int] = mapped_column(Integer)
    order_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[str] = mapped_column(String(32))
    tracking_code: Mapped[str | None] = mapped_column(String(64))
    estimated_delivery: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)


class CommerceOrderItem(UUIDPk, TenantScoped, Base):
    __tablename__ = "commerce_order_items"
    __table_args__ = (UniqueConstraint("tenant_id", "order_external_id", "product_external_id"),)

    order_external_id: Mapped[int] = mapped_column(Integer, index=True)
    product_external_id: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
