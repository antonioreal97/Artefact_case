"""``CommerceBackend`` over the tenant-scoped ``commerce_*`` tables.

Every query filters ``tenant_id`` in SQL; the tenant is fixed at construction. Catalogs are
small, so text search runs in Python over a tenant-filtered fetch (case/accent-insensitive).
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.base import (
    BackendUnavailableError,
    OrderRecord,
    ProductDetails,
    ProductQuery,
    ProductStatus,
    ProductSummary,
    Promotion,
    StockInfo,
)
from app.models.commerce import (
    CommerceCategory,
    CommerceCustomer,
    CommerceOrder,
    CommerceProduct,
    CommercePromotion,
)

MAX_SEARCH_LIMIT = 20
_CENT = Decimal("0.01")
_STOPWORDS = frozenset(
    {"a", "as", "o", "os", "e", "de", "da", "do", "das", "dos", "um", "uma", "para", "com"}
)
_DB_ERRORS = (SQLAlchemyError, OSError)
_UNAVAILABLE = "commerce backend unavailable"


def normalize_text(value: str) -> str:
    """Casefold and strip accents (``"Violão"`` -> ``"violao"``)."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def promotional_price(price: Decimal, discount_percent: Decimal) -> Decimal:
    return (price * (Decimal(100) - discount_percent) / Decimal(100)).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )


def _best_promotion(price: Decimal, promos: Sequence[CommercePromotion]) -> Promotion | None:
    if not promos:
        return None
    best = max(promos, key=lambda p: (p.discount_percent, -p.external_id))
    return Promotion(
        description=best.description,
        discount_percent=best.discount_percent,
        promotional_price=promotional_price(price, best.discount_percent),
    )


def _status(value: str) -> ProductStatus:
    if value == "active":
        return "active"
    if value == "discontinued":
        return "discontinued"
    if value == "coming_soon":
        return "coming_soon"
    raise ValueError("unknown product status")


class LocalCommerceBackend:
    """Bound to exactly one tenant. Methods never take a tenant id from the caller."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
    ) -> None:
        self._session_factory = session_factory
        self.tenant_id = tenant_id

    # --- helpers -----------------------------------------------------------------------
    async def _categories(self, session: AsyncSession) -> dict[int, str]:
        rows = await session.execute(
            select(CommerceCategory.external_id, CommerceCategory.name).where(
                CommerceCategory.tenant_id == self.tenant_id
            )
        )
        return {ext_id: name for ext_id, name in rows.all()}

    async def _active_promotions(
        self, session: AsyncSession, product_ids: Sequence[int] | None = None
    ) -> dict[int, list[CommercePromotion]]:
        stmt = select(CommercePromotion).where(
            CommercePromotion.tenant_id == self.tenant_id,
            CommercePromotion.is_active.is_(True),
        )
        if product_ids is not None:
            stmt = stmt.where(CommercePromotion.product_external_id.in_(product_ids))
        promos: dict[int, list[CommercePromotion]] = {}
        for promo in (await session.scalars(stmt)).all():
            promos.setdefault(promo.product_external_id, []).append(promo)
        return promos

    async def _product_row(self, session: AsyncSession, product_id: int) -> CommerceProduct | None:
        return await session.scalar(
            select(CommerceProduct).where(
                CommerceProduct.tenant_id == self.tenant_id,
                CommerceProduct.external_id == product_id,
            )
        )

    @staticmethod
    def _summary(
        product: CommerceProduct, category: str, promos: Sequence[CommercePromotion]
    ) -> ProductSummary:
        status = _status(product.status)
        return ProductSummary(
            product_id=product.external_id,
            name=product.name,
            category=category,
            price=product.price,
            currency=product.currency,
            status=status,
            available=status == "active" and product.stock_quantity > 0,
            active_promotion=_best_promotion(product.price, promos),
        )

    # --- CommerceBackend ---------------------------------------------------------------
    async def search_products(self, query: ProductQuery) -> list[ProductSummary]:
        limit = max(1, min(query.limit, MAX_SEARCH_LIMIT))
        try:
            async with self._session_factory() as session:
                categories = await self._categories(session)
                products = (
                    await session.scalars(
                        select(CommerceProduct)
                        .where(CommerceProduct.tenant_id == self.tenant_id)
                        .order_by(CommerceProduct.external_id)
                    )
                ).all()
                promos = await self._active_promotions(session)
        except _DB_ERRORS:
            raise BackendUnavailableError(_UNAVAILABLE) from None

        terms = [t for t in normalize_text(query.text or "").split() if t not in _STOPWORDS]
        category_filter = normalize_text(query.category).strip() if query.category else ""

        scored: list[tuple[int, ProductSummary]] = []
        for product in products:
            category = categories.get(product.category_external_id, "")
            norm_category = normalize_text(category)
            if category_filter and category_filter not in norm_category:
                continue
            norm_name = normalize_text(product.name)
            haystack = f"{norm_name} {normalize_text(product.description)} {norm_category}"
            if not all(term in haystack for term in terms):
                continue
            summary = self._summary(product, category, promos.get(product.external_id, []))
            if query.in_stock_only and not summary.available:
                continue
            # Price filters apply to what the customer pays (promotional price if any).
            effective = (
                summary.active_promotion.promotional_price
                if summary.active_promotion
                else summary.price
            )
            if query.min_price is not None and effective < query.min_price:
                continue
            if query.max_price is not None and effective > query.max_price:
                continue
            name_hits = sum(1 for term in terms if term in norm_name)
            scored.append((name_hits, summary))

        scored.sort(key=lambda item: (-item[0], not item[1].available, item[1].product_id))
        return [summary for _, summary in scored[:limit]]

    async def get_product(self, product_id: int) -> ProductDetails | None:
        try:
            async with self._session_factory() as session:
                product = await self._product_row(session, product_id)
                if product is None:
                    return None
                category = await session.scalar(
                    select(CommerceCategory.name).where(
                        CommerceCategory.tenant_id == self.tenant_id,
                        CommerceCategory.external_id == product.category_external_id,
                    )
                )
                promos = await self._active_promotions(session, [product_id])
        except _DB_ERRORS:
            raise BackendUnavailableError(_UNAVAILABLE) from None
        summary = self._summary(product, category or "", promos.get(product_id, []))
        return ProductDetails(
            **summary.model_dump(),
            description=product.description,
            specs=dict(product.specs or {}),
            stock_quantity=product.stock_quantity,
        )

    async def get_stock(self, product_id: int) -> StockInfo | None:
        try:
            async with self._session_factory() as session:
                product = await self._product_row(session, product_id)
        except _DB_ERRORS:
            raise BackendUnavailableError(_UNAVAILABLE) from None
        if product is None:
            return None
        status = _status(product.status)
        return StockInfo(
            product_id=product.external_id,
            name=product.name,
            status=status,
            stock_quantity=product.stock_quantity,
            available=status == "active" and product.stock_quantity > 0,
        )

    async def get_order(self, order_id: int) -> OrderRecord | None:
        # One JOIN query whether or not the order exists (review L-6: no second round trip
        # that would reveal order existence through timing). Both sides filter the tenant.
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(CommerceOrder, CommerceCustomer)
                        .outerjoin(
                            CommerceCustomer,
                            (CommerceCustomer.tenant_id == CommerceOrder.tenant_id)
                            & (CommerceCustomer.external_id == CommerceOrder.customer_external_id),
                        )
                        .where(
                            CommerceOrder.tenant_id == self.tenant_id,
                            CommerceOrder.external_id == order_id,
                        )
                    )
                ).first()
        except _DB_ERRORS:
            raise BackendUnavailableError(_UNAVAILABLE) from None
        if row is None:
            return None
        order, customer = row
        if customer is None:
            return None  # cannot be verified -> treated like a missing order
        return OrderRecord(
            order_id=order.external_id,
            status=order.status,
            order_date=order.order_date,
            tracking_code=order.tracking_code,
            estimated_delivery=order.estimated_delivery,
            payment_method=order.payment_method,
            total=order.total,
            customer_phone=customer.phone,
            customer_email=customer.email,
        )
