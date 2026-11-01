"""Local token-vault persistence. Callers fail closed on VaultStoreUnavailable."""

from __future__ import annotations

import json
import time
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.services.privacy_vault import PrivacyVault

VAULT_TTL_SECONDS = 24 * 60 * 60


class VaultStoreUnavailable(RuntimeError):
    """No restricted flow may proceed while vault persistence is unavailable."""


class VaultStore(Protocol):
    async def load(self, tenant_id: UUID, conversation_id: UUID) -> PrivacyVault: ...

    async def save(self, tenant_id: UUID, conversation_id: UUID, vault: PrivacyVault) -> None: ...


def _key(tenant_id: UUID, conversation_id: UUID) -> str:
    return f"vault:{tenant_id}:{conversation_id}"


class RedisVaultStore:
    def __init__(self, settings: Settings) -> None:
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    async def load(self, tenant_id: UUID, conversation_id: UUID) -> PrivacyVault:
        try:
            raw = await self.redis.get(_key(tenant_id, conversation_id))
            state = json.loads(raw) if raw else None
            if state is not None and not isinstance(state, dict):
                raise ValueError("Invalid vault state")
            return PrivacyVault.from_state(state)
        except (RedisError, OSError, ValueError, TypeError, AssertionError, KeyError) as exc:
            raise VaultStoreUnavailable("Vault unavailable") from exc

    async def save(self, tenant_id: UUID, conversation_id: UUID, vault: PrivacyVault) -> None:
        try:
            await self.redis.set(
                _key(tenant_id, conversation_id),
                json.dumps(vault.to_state()),
                ex=VAULT_TTL_SECONDS,
            )
        except (RedisError, OSError) as exc:
            raise VaultStoreUnavailable("Vault unavailable") from exc

    async def aclose(self) -> None:
        await self.redis.aclose()


class InMemoryVaultStore:
    """Process-local development/test store, with the same expiry and copy semantics."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, str]] = {}

    async def load(self, tenant_id: UUID, conversation_id: UUID) -> PrivacyVault:
        key = _key(tenant_id, conversation_id)
        entry = self._values.get(key)
        if entry is None or entry[0] <= time.monotonic():
            self._values.pop(key, None)
            return PrivacyVault()
        return PrivacyVault.from_state(json.loads(entry[1]))

    async def save(self, tenant_id: UUID, conversation_id: UUID, vault: PrivacyVault) -> None:
        self._values[_key(tenant_id, conversation_id)] = (
            time.monotonic() + VAULT_TTL_SECONDS,
            json.dumps(vault.to_state()),
        )
