"""Validate a complete local commerce export before any tenant-scoped writes."""

from __future__ import annotations

import csv
import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base
from app.models import (
    CommerceCategory,
    CommerceCustomer,
    CommerceOrder,
    CommerceOrderItem,
    CommerceProduct,
    CommercePromotion,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "Desafio Técnico AI Engineer 2026" / "data"
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
PositiveId = Annotated[int, Field(gt=0)]


def _empty_to_none(value: object) -> object:
    return None if value == "" else value


def _reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _parse_specs(value: str) -> object:
    return json.loads(value, parse_constant=_reject_json_constant)


OptionalDate = Annotated[date | None, BeforeValidator(_empty_to_none)]
OptionalText = Annotated[str | None, BeforeValidator(_empty_to_none)]


class ExportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CategoryRow(ExportRow):
    external_id: PositiveId = Field(alias="category_id")
    name: str = Field(min_length=1, max_length=200)
    description: str


class ProductRow(ExportRow):
    external_id: PositiveId = Field(alias="product_id")
    category_external_id: PositiveId = Field(alias="category_id")
    name: str = Field(min_length=1, max_length=300)
    description: str
    price: Money = Field(alias="price_brl")
    stock_quantity: int = Field(ge=0)
    status: Literal["active", "discontinued", "coming_soon"]
    specs: Annotated[dict[str, Any], BeforeValidator(_parse_specs)]
    created_on: OptionalDate = Field(alias="created_at")


class PromotionRow(ExportRow):
    external_id: PositiveId = Field(alias="promotion_id")
    product_external_id: PositiveId = Field(alias="product_id")
    discount_percent: Annotated[
        Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2, allow_inf_nan=False)
    ]
    description: str = Field(max_length=200)
    is_active: Literal["0", "1"]


class CustomerRow(ExportRow):
    external_id: PositiveId = Field(alias="customer_id")
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=32)
    email: str = Field(min_length=1, max_length=200)
    city: OptionalText = Field(max_length=100)


class OrderRow(ExportRow):
    external_id: PositiveId = Field(alias="order_id")
    customer_external_id: PositiveId = Field(alias="customer_id")
    order_date: date
    status: Literal["pending", "confirmed", "shipped", "delivered", "cancelled"]
    total: Money = Field(alias="total_brl")
    payment_method: Literal["pix", "debit", "boleto", "credit_3x", "credit_6x", "credit_12x"]
    tracking_code: OptionalText = Field(max_length=64)
    estimated_delivery: OptionalDate
    notes: OptionalText


class OrderItemRow(ExportRow):
    order_external_id: PositiveId = Field(alias="order_id")
    product_external_id: PositiveId = Field(alias="product_id")
    quantity: int = Field(gt=0)


EXPORTS: dict[str, tuple[type[ExportRow], type[Base]]] = {
    "categories": (CategoryRow, CommerceCategory),
    "products": (ProductRow, CommerceProduct),
    "promotions": (PromotionRow, CommercePromotion),
    "customers": (CustomerRow, CommerceCustomer),
    "orders": (OrderRow, CommerceOrder),
    "order_items": (OrderItemRow, CommerceOrderItem),
}


def load_commerce(data_dir: Path = DATA_DIR) -> dict[str, list[dict[str, Any]]]:
    """Reject malformed rows, duplicate keys and dangling references without exposing values."""
    exports: dict[str, list[dict[str, Any]]] = {}
    for name, (schema, _) in EXPORTS.items():
        records: list[dict[str, Any]] = []
        seen: set[object] = set()
        with (data_dir / f"desafio_tecnico_ai_eng - {name}.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            reader = csv.DictReader(stream)
            expected = {field.alias or key for key, field in schema.model_fields.items()}
            if (
                reader.fieldnames is None
                or set(reader.fieldnames) != expected
                or len(reader.fieldnames) != len(expected)
            ):
                raise ValueError(f"Invalid {name} CSV header")
            for line, row in enumerate(reader, start=2):
                try:
                    record = schema.model_validate(row).model_dump()
                except (ValidationError, ValueError, TypeError):
                    raise ValueError(f"Invalid {name} CSV row at line {line}") from None
                key = (
                    (record["order_external_id"], record["product_external_id"])
                    if name == "order_items"
                    else record["external_id"]
                )
                if key in seen:
                    raise ValueError(f"Duplicate key in {name} CSV at line {line}")
                seen.add(key)
                if name == "promotions":
                    record["is_active"] = record["is_active"] == "1"
                if name == "products":
                    record["currency"] = "BRL"
                records.append(record)
        exports[name] = records
    for child, field, parent in [
        ("products", "category_external_id", "categories"),
        ("promotions", "product_external_id", "products"),
        ("orders", "customer_external_id", "customers"),
        ("order_items", "order_external_id", "orders"),
        ("order_items", "product_external_id", "products"),
    ]:
        parent_ids = {row["external_id"] for row in exports[parent]}
        if any(row[field] not in parent_ids for row in exports[child]):
            raise ValueError(f"Orphan reference in {child} CSV ({field})")
    return exports


async def import_commerce(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    exports: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    """Upsert a validated export; the caller owns the transaction."""
    for name, (_, model) in EXPORTS.items():
        for record in exports[name]:
            values = {**record, "tenant_id": tenant_id}
            statement = insert(model).values(**values)
            tenant_column = model.__table__.c.tenant_id
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=(
                        ["tenant_id", "order_external_id", "product_external_id"]
                        if name == "order_items"
                        else ["tenant_id", "external_id"]
                    ),
                    set_={key: getattr(statement.excluded, key) for key in record},
                    where=tenant_column == tenant_id,
                )
            )
    return {name: len(rows) for name, rows in exports.items()}
