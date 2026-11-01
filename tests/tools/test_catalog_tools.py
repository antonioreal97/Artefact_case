from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.integrations.base import BackendUnavailableError
from app.integrations.local_commerce import LocalCommerceBackend, promotional_price
from app.tools.catalog import get_price, get_product_details, search_products
from app.tools.inventory import check_stock

pytestmark = pytest.mark.integration


def ids(result) -> list[int]:
    assert result.ok, result.error
    return [p.product_id for p in result.data.items]


# --- search ------------------------------------------------------------------------------
async def test_search_text_is_case_and_accent_insensitive(ctx_a):
    assert sorted(ids(await search_products(ctx_a, query="VIOLAO"))) == [4, 5]
    assert ids(await search_products(ctx_a, query="violão nylon")) == [4]
    # matches category name too ("Guitarras")
    assert sorted(ids(await search_products(ctx_a, query="guitarra"))) == [1, 2, 3]
    assert ids(await search_products(ctx_a, query="inexistente xyz")) == []


async def test_search_category_price_stock_and_limit(ctx_a):
    assert sorted(ids(await search_products(ctx_a, category="violoes"))) == [4, 5]
    assert sorted(ids(await search_products(ctx_a, category="Guitarras", limit=10))) == [1, 2, 3]
    # price filters use the effective (promotional) price: product 4 -> 509.92
    assert sorted(ids(await search_products(ctx_a, max_price=Decimal("1000")))) == [4, 5]
    assert ids(await search_products(ctx_a, min_price="510", max_price="1000")) == [5]
    # product 1 list price 7999 but promo 6999.13 -> excluded from >= 7000
    assert ids(await search_products(ctx_a, min_price=7000)) == [2]
    assert sorted(ids(await search_products(ctx_a, in_stock_only=True))) == [1, 4]
    res = await search_products(ctx_a, limit=2)
    assert res.ok and len(res.data.items) == 2 and res.data.total == 2


async def test_search_invalid_input(ctx_a):
    for kwargs in (
        {"limit": 50},
        {"limit": 0},
        {"min_price": -1},
        {"min_price": 10, "max_price": 5},
    ):
        res = await search_products(ctx_a, **kwargs)
        assert not res.ok and res.error.code == "invalid_input"
    assert ctx_a.tool_calls[-1].status == "error"
    assert ctx_a.tool_calls[-1].error_code == "invalid_input"


async def test_promotion_math_highest_active_half_up(ctx_a):
    price = await get_price(ctx_a, 4)
    assert price.ok
    promo = price.data.active_promotion
    assert promo.discount_percent == Decimal("15")
    assert promo.promotional_price == Decimal("509.92")
    assert price.data.price == Decimal("599.90") and price.data.currency == "BRL"

    strat = await get_price(ctx_a, 1)
    assert strat.data.active_promotion.promotional_price == Decimal("6999.13")  # not 6999.12
    assert strat.data.active_promotion.description == "Semana do Rock"  # inactive 20% ignored

    no_promo = await get_price(ctx_a, 2)
    assert no_promo.data.active_promotion is None
    assert promotional_price(Decimal("10.00"), Decimal("33.35")) == Decimal("6.67")


# --- details / stock / statuses ----------------------------------------------------------
async def test_details_and_stock_statuses(ctx_a):
    details = await get_product_details(ctx_a, 4)
    assert details.ok
    assert details.data.category == "Violões"
    assert details.data.specs == {"strings": "nylon"}
    assert details.data.available and details.data.stock_quantity == 12

    cases = {
        2: ("active", False),  # out of stock
        3: ("discontinued", False),  # has stock but discontinued
        5: ("coming_soon", False),
        1: ("active", True),
    }
    for product_id, (status, available) in cases.items():
        stock = await check_stock(ctx_a, product_id)
        assert stock.ok
        assert (stock.data.status, stock.data.available) == (status, available)
        details = await get_product_details(ctx_a, product_id)
        assert (details.data.status, details.data.available) == (status, available)


async def test_not_found_and_invalid_product_id(ctx_a):
    for tool in (get_product_details, get_price, check_stock):
        res = await tool(ctx_a, 9999)
        assert not res.ok and res.error.code == "not_found"
        res = await tool(ctx_a, "abc")
        assert not res.ok and res.error.code == "invalid_input"
    assert all(rec.status == "error" for rec in ctx_a.tool_calls)


# --- tenant isolation --------------------------------------------------------------------
async def test_cross_tenant_catalog_isolation(ctx_a, ctx_b):
    # Tenant B cannot see A's products even with valid ids.
    for product_id in (2, 3, 4, 5):
        assert (await get_product_details(ctx_b, product_id)).error.code == "not_found"
        assert (await check_stock(ctx_b, product_id)).error.code == "not_found"
    # Same external id 1 resolves to each tenant's own product.
    assert (await get_product_details(ctx_a, 1)).data.name == "Fender Stratocaster Player"
    assert (await get_product_details(ctx_b, 1)).data.name == "Teclado Sintético X"
    assert ids(await search_products(ctx_b, query="violao")) == []
    assert ids(await search_products(ctx_b, limit=20)) == [1]
    assert ids(await search_products(ctx_a, query="teclado")) == []


# --- failures: timeout / backend outage ---------------------------------------------------
class SlowBackend:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id

    async def search_products(self, query):
        await asyncio.sleep(5)
        return []

    async def get_product(self, product_id):
        await asyncio.sleep(5)

    async def get_stock(self, product_id):
        await asyncio.sleep(5)

    async def get_order(self, order_id):
        await asyncio.sleep(5)


class BrokenBackend(SlowBackend):
    async def search_products(self, query):
        raise BackendUnavailableError("down")

    async def get_product(self, product_id):
        raise BackendUnavailableError("down")

    async def get_stock(self, product_id):
        raise RuntimeError("boom with secret detail")


async def test_timeout_maps_to_unavailable(ctx_factory, seed, monkeypatch):
    monkeypatch.setattr(get_settings(), "tool_timeout_seconds", 0.05)
    ctx = ctx_factory(SlowBackend(seed.tenant_a))
    for coro in (search_products(ctx, query="x"), get_price(ctx, 1), check_stock(ctx, 1)):
        res = await coro
        assert not res.ok and res.error.code == "unavailable"
    assert [r.error_code for r in ctx.tool_calls] == ["unavailable"] * 3
    assert all(r.latency_ms < 2000 for r in ctx.tool_calls)


async def test_backend_errors_map_to_safe_failures(ctx_factory, seed):
    ctx = ctx_factory(BrokenBackend(seed.tenant_a))
    assert (await search_products(ctx, query="x")).error.code == "unavailable"
    assert (await get_product_details(ctx, 1)).error.code == "unavailable"
    internal = await check_stock(ctx, 1)
    assert internal.error.code == "internal_error"
    assert "secret" not in internal.error.message


async def test_local_backend_db_outage_is_unavailable(ctx_factory, seed):
    engine = create_async_engine("postgresql+asyncpg://nobody:nobody@127.0.0.1:1/none")
    try:
        dead = async_sessionmaker(engine)
        backend = LocalCommerceBackend(dead, seed.tenant_a)
        with pytest.raises(BackendUnavailableError):
            await backend.get_product(1)
        ctx = ctx_factory(backend)
        assert (await search_products(ctx, query="x")).error.code == "unavailable"
    finally:
        await engine.dispose()


async def test_tool_call_records(ctx_a):
    await search_products(ctx_a, query="violão para lucas@example.com", limit=3)
    await get_price(ctx_a, 4)
    search_rec, price_rec = ctx_a.tool_calls
    assert search_rec.tool_name == "search_products" and search_rec.status == "success"
    assert "lucas@example.com" not in str(search_rec.arguments_redacted)
    assert search_rec.result_summary == {"result_count": 0}
    assert price_rec.result_summary == {"found": True, "has_promotion": True}
    assert price_rec.latency_ms >= 0
