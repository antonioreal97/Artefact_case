"""Attempt limiter contract (orchestrator-owned). Counts failures per key inside a window,
across conversations and processes. ``RedisAttemptLimiter`` (Redis INCR + EXPIRE) is the
runtime implementation; ``InMemoryAttemptLimiter`` is for tests and single-process dev."""

from __future__ import annotations

import time
from typing import Protocol


class AttemptLimiter(Protocol):
    async def failures(self, key: str) -> int: ...

    async def record_failure(self, key: str, window_seconds: int) -> int: ...

    async def hit(self, key: str, window_seconds: int) -> int:
        """Count one request (rate limiting); returns the count inside the window."""
        ...


class InMemoryAttemptLimiter:
    def __init__(self) -> None:
        self._counts: dict[str, tuple[int, float]] = {}

    def _get(self, key: str) -> int:
        count, expires = self._counts.get(key, (0, 0.0))
        if expires and expires < time.monotonic():
            self._counts.pop(key, None)
            return 0
        return count

    async def failures(self, key: str) -> int:
        return self._get(key)

    async def record_failure(self, key: str, window_seconds: int) -> int:
        return await self.hit(key, window_seconds)

    async def hit(self, key: str, window_seconds: int) -> int:
        count = self._get(key) + 1
        _, expires = self._counts.get(key, (0, 0.0))
        self._counts[key] = (count, expires or time.monotonic() + window_seconds)
        return count
