"""Redis implementation of the ``AttemptLimiter`` contract (cross-process, cross-conversation).

Each key is a fixed-window counter: the first hit creates it with ``window_seconds`` expiry,
later hits only increment. INCR and EXPIRE run in one Lua script, so concurrent requests
never lose increments and a counter can never be left without a TTL. Callers build keys
that already include the tenant (``orderfail:{tenant}:…``, ``rate:{tenant}:…``); this module
adds the ``limiter:`` namespace.

Errors are raised to the caller on purpose: the order lookup fails closed, request rate
limiting fails open (availability). Exception messages are never logged here (a Redis URL
may carry credentials).
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from app.core.config import Settings

KEY_PREFIX = "limiter:"

# Returns the post-increment count. Re-applies the expiry when the key has none
# (defensive: e.g. a key written by an older client without TTL).
_INCR_WITH_EXPIRY = """
local count = redis.call('INCR', KEYS[1])
if count == 1 or redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class RedisAttemptLimiter:
    def __init__(self, redis: Any) -> None:
        # ``Any``: redis.asyncio.Redis at runtime; tests may pass a compatible fake.
        self._redis = redis

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisAttemptLimiter:
        return cls(Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1))

    async def failures(self, key: str) -> int:
        value = await self._redis.get(KEY_PREFIX + key)
        return int(value) if value is not None else 0

    async def record_failure(self, key: str, window_seconds: int) -> int:
        return await self.hit(key, window_seconds)

    async def hit(self, key: str, window_seconds: int) -> int:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        count = await self._redis.eval(_INCR_WITH_EXPIRY, 1, KEY_PREFIX + key, window_seconds)
        return int(count)

    async def aclose(self) -> None:
        close = getattr(self._redis, "aclose", None)
        if close is not None:
            await close()


__all__ = ["KEY_PREFIX", "RedisAttemptLimiter"]
