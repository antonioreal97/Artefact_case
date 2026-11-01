"""Catalog tools (public data): product search, details and price.

Also hosts ``run_tool``, the shared execution wrapper used by every commerce tool
(validation -> timeout -> error mapping -> redacted ``ToolCallRecord``). It lives here
because the tool package ownership is limited to the tool modules themselves.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.integrations.base import (
    BackendUnavailableError,
    ProductDetails,
    ProductQuery,
    ProductStatus,
    ProductSummary,
    Promotion,
)
from app.schemas.tools import ToolResult
from app.tools.context import ToolCallRecord, ToolContext
from emporio_agent.privacy import redact_for_log

logger = logging.getLogger(__name__)

MSG_INVALID = "Não entendi os dados informados para esta consulta. Pode verificar e tentar de novo?"
MSG_UNAVAILABLE = (
    "O sistema da loja está temporariamente indisponível. Tente novamente em instantes "
    "ou peça para falar com um atendente."
)
MSG_INTERNAL = "Não foi possível concluir a consulta agora. Posso encaminhar para um atendente."
MSG_PRODUCT_NOT_FOUND = "Não encontrei esse produto no catálogo da loja."

_MAX_ARG_CHARS = 120


def redact_arguments(arguments: Mapping[str, object]) -> dict[str, object]:
    """Redacted, size-bounded copy of tool arguments for the audit record."""
    redacted = redact_for_log(dict(arguments))
    out: dict[str, object] = {}
    for key, value in redacted.items():
        if isinstance(value, Decimal):
            value = str(value)
        if isinstance(value, str) and len(value) > _MAX_ARG_CHARS:
            value = value[:_MAX_ARG_CHARS] + "…"
        out[str(key)] = value
    return out


async def run_tool[T](
    ctx: ToolContext,
    tool_name: str,
    arguments: Mapping[str, object],
    operation: Callable[[], Awaitable[ToolResult[T]]],
    summarize: Callable[[T], dict[str, object]],
) -> ToolResult[T]:
    """Run ``operation`` under the tool timeout; never raise; always record the call.

    ``operation`` performs input validation itself (``ValidationError`` -> invalid_input).
    """
    started = time.perf_counter()
    result: ToolResult[T]
    try:
        async with asyncio.timeout(get_settings().tool_timeout_seconds):
            result = await operation()
    except ValidationError:
        result = ToolResult.failure("invalid_input", MSG_INVALID)
    except (TimeoutError, BackendUnavailableError, SQLAlchemyError, OSError):
        result = ToolResult.failure("unavailable", MSG_UNAVAILABLE)
    except Exception as exc:
        # Type name only: exception messages may carry restricted values.
        logger.error("tool %s failed: %s", tool_name, type(exc).__name__)
        result = ToolResult.failure("internal_error", MSG_INTERNAL)

    summary: dict[str, object]
    if result.ok and result.data is not None:
        summary = summarize(result.data)
    else:
        summary = {"error_code": result.error.code if result.error else "internal_error"}
    ctx.tool_calls.append(
        ToolCallRecord(
            tool_name=tool_name,
            arguments_redacted=redact_arguments(arguments),
            status="success" if result.ok else "error",
            error_code=result.error.code if result.error else None,
            result_summary=summary,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    )
    return result


# --- input / output models ---------------------------------------------------------------
class SearchProductsInput(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    min_price: Decimal | None = Field(default=None, ge=0)
    max_price: Decimal | None = Field(default=None, ge=0)
    in_stock_only: bool = False
    limit: int = Field(default=5, ge=1, le=20)


class ProductIdInput(BaseModel):
    product_id: int = Field(gt=0, lt=2**31)


class ProductSearchResult(BaseModel):
    items: list[ProductSummary]
    total: int


class PriceView(BaseModel):
    product_id: int
    name: str
    price: Decimal  # list price
    currency: str
    status: ProductStatus
    available: bool
    active_promotion: Promotion | None = None


# --- tools -------------------------------------------------------------------------------
async def search_products(
    ctx: ToolContext,
    query: str | None = None,
    category: str | None = None,
    min_price: Decimal | float | str | None = None,
    max_price: Decimal | float | str | None = None,
    in_stock_only: bool = False,
    limit: int = 5,
) -> ToolResult[ProductSearchResult]:
    """Search the tenant catalog by text/category/price range/stock."""
    args: dict[str, Any] = {
        "query": query,
        "category": category,
        "min_price": min_price,
        "max_price": max_price,
        "in_stock_only": in_stock_only,
        "limit": limit,
    }

    async def op() -> ToolResult[ProductSearchResult]:
        params = SearchProductsInput.model_validate(args)
        if (
            params.min_price is not None
            and params.max_price is not None
            and params.min_price > params.max_price
        ):
            return ToolResult.failure("invalid_input", "O preço mínimo é maior que o máximo.")
        items = await ctx.commerce.search_products(
            ProductQuery(
                text=(params.query or "").strip() or None,
                category=(params.category or "").strip() or None,
                min_price=params.min_price,
                max_price=params.max_price,
                in_stock_only=params.in_stock_only,
                limit=params.limit,
            )
        )
        items = items[: params.limit]
        return ToolResult.success(ProductSearchResult(items=items, total=len(items)))

    return await run_tool(
        ctx,
        "search_products",
        args,
        op,
        lambda data: {"result_count": data.total},
    )


async def get_product_details(ctx: ToolContext, product_id: int) -> ToolResult[ProductDetails]:
    """Full public details of one product (description, specs, stock, promotion)."""
    args: dict[str, Any] = {"product_id": product_id}

    async def op() -> ToolResult[ProductDetails]:
        params = ProductIdInput.model_validate(args)
        product = await ctx.commerce.get_product(params.product_id)
        if product is None:
            return ToolResult.failure("not_found", MSG_PRODUCT_NOT_FOUND)
        return ToolResult.success(product)

    return await run_tool(
        ctx,
        "get_product_details",
        args,
        op,
        lambda data: {"found": True, "status": data.status, "available": data.available},
    )


async def get_price(ctx: ToolContext, product_id: int) -> ToolResult[PriceView]:
    """List price, currency, status and the active promotion (if any) of one product."""
    args: dict[str, Any] = {"product_id": product_id}

    async def op() -> ToolResult[PriceView]:
        params = ProductIdInput.model_validate(args)
        product = await ctx.commerce.get_product(params.product_id)
        if product is None:
            return ToolResult.failure("not_found", MSG_PRODUCT_NOT_FOUND)
        return ToolResult.success(
            PriceView(
                product_id=product.product_id,
                name=product.name,
                price=product.price,
                currency=product.currency,
                status=product.status,
                available=product.available,
                active_promotion=product.active_promotion,
            )
        )

    return await run_tool(
        ctx,
        "get_price",
        args,
        op,
        lambda data: {"found": True, "has_promotion": data.active_promotion is not None},
    )
