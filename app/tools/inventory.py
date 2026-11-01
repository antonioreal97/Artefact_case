"""Inventory tool (public data): stock of one product."""

from __future__ import annotations

from typing import Any

from app.integrations.base import StockInfo
from app.schemas.tools import ToolResult
from app.tools.catalog import MSG_PRODUCT_NOT_FOUND, ProductIdInput, run_tool
from app.tools.context import ToolContext


async def check_stock(ctx: ToolContext, product_id: int) -> ToolResult[StockInfo]:
    """Stock quantity, status and availability (active and stock > 0) of one product."""
    args: dict[str, Any] = {"product_id": product_id}

    async def op() -> ToolResult[StockInfo]:
        params = ProductIdInput.model_validate(args)
        stock = await ctx.commerce.get_stock(params.product_id)
        if stock is None:
            return ToolResult.failure("not_found", MSG_PRODUCT_NOT_FOUND)
        return ToolResult.success(stock)

    return await run_tool(
        ctx,
        "check_stock",
        args,
        op,
        lambda data: {"found": True, "status": data.status, "available": data.available},
    )
