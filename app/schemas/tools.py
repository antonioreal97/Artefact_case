"""Typed tool result envelope. The agent may only claim success when ``ok`` is True."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ToolErrorCode = Literal[
    "not_found",
    "invalid_input",
    "not_authorized",  # e.g. order identity verification failed
    "unavailable",  # backend timeout / outage
    "internal_error",
]


class ToolError(BaseModel):
    code: ToolErrorCode
    message: str  # safe for the model and the customer; never a stack trace or PII


class ToolResult[T](BaseModel):
    ok: bool
    data: T | None = None
    error: ToolError | None = None

    @classmethod
    def success(cls, data: T) -> ToolResult[T]:
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, code: ToolErrorCode, message: str) -> ToolResult[T]:
        return cls(ok=False, error=ToolError(code=code, message=message))
