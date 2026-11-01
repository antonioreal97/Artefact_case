"""RedisAttemptLimiter against the compose Redis (skipped when unreachable)."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.services.attempt_limiter_redis import KEY_PREFIX, RedisAttemptLimiter

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/15")


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=2)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        await client.aclose()
        pytest.skip(f"Redis not reachable: {type(exc).__name__}")
    try:
        yield client
    finally:
        await client.aclose()


async def test_concurrent_hits_are_atomic_and_expire(redis: Redis) -> None:
    limiter = RedisAttemptLimiter(redis)
    key = f"orderfail:{uuid.uuid4()}:actor:test"
    results = await asyncio.gather(*(limiter.record_failure(key, 60) for _ in range(25)))
    assert sorted(results) == list(range(1, 26))  # no lost increments
    assert await limiter.failures(key) == 25
    ttl = await redis.ttl(KEY_PREFIX + key)
    assert 0 < ttl <= 60  # window set once, not extended by later hits
    await redis.delete(KEY_PREFIX + key)


async def test_missing_key_counts_zero_and_window_resets(redis: Redis) -> None:
    limiter = RedisAttemptLimiter(redis)
    key = f"rate:{uuid.uuid4()}:chat"
    assert await limiter.failures(key) == 0
    assert await limiter.hit(key, 1) == 1
    await asyncio.sleep(1.2)
    assert await limiter.failures(key) == 0
    assert await limiter.hit(key, 1) == 1


async def test_key_without_ttl_gets_one(redis: Redis) -> None:
    limiter = RedisAttemptLimiter(redis)
    key = f"orderfail:{uuid.uuid4()}:order:x"
    await redis.set(KEY_PREFIX + key, 5)  # legacy key without expiry
    assert await limiter.record_failure(key, 30) == 6
    assert 0 < await redis.ttl(KEY_PREFIX + key) <= 30
    await redis.delete(KEY_PREFIX + key)


async def test_errors_propagate() -> None:
    limiter = RedisAttemptLimiter(
        Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2)
    )
    with pytest.raises(Exception):  # any connection error; callers decide
        await limiter.hit("k", 10)
    await limiter.aclose()
