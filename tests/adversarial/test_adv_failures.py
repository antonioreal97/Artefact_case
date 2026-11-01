"""Attack 10: failure paths (commerce backend, DB, Redis/vault, embeddings, model) must give a
safe reply, never a stack trace or PII, and create a handoff where required."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from adv_support import World, assert_safe_error, chat, leaked, restricted_values, scripted
from agents.testing import ModelStep, assistant_message
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import get_session
from app.integrations.base import BackendUnavailableError
from app.integrations.local_commerce import LocalCommerceBackend
from app.rag.embeddings import HashEmbeddingProvider
from app.services.vault_store import RedisVaultStore

pytestmark = pytest.mark.integration

LEAKY = "ana.teste@example.test BRAA1234567BR /srv/app/secret.py password=hunter2"
NEEDLES = ["ana.teste@example.test", "BRAA1234567BR", "secret.py", "hunter2", "Traceback"]


async def _assert_handoff(client, tenant, reply: dict, reasons: set[str]) -> None:
    assert reply["handoff"] is not None, reply
    assert reply["handoff"]["reason"] in reasons, reply
    listed = await client.get(
        f"/v1/handoffs/{reply['handoff']['handoff_id']}", headers=tenant.headers
    )
    assert listed.status_code == 200 and listed.json()["reason"] in reasons


def _assert_clean(reply: dict, logs_text: str) -> None:
    assert not leaked(reply["reply"], NEEDLES), reply
    assert not leaked(logs_text, NEEDLES), leaked(logs_text, NEEDLES)


@pytest.mark.parametrize(
    "error",
    [
        BackendUnavailableError("commerce backend unavailable"),
        RuntimeError(LEAKY),
        OperationalError(
            f"SELECT * FROM orders WHERE email='{LEAKY}'", {"p": LEAKY}, OSError(LEAKY)
        ),
        TimeoutError(LEAKY),
    ],
    ids=["unavailable", "runtime-with-pii", "db-operational", "timeout"],
)
async def test_commerce_backend_failures_are_safe(
    client, world: World, monkeypatch, logs, model_control, error: Exception
) -> None:
    async def broken(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(LocalCommerceBackend, "search_products", broken)
    monkeypatch.setattr(LocalCommerceBackend, "get_order", broken)
    price = await chat(client, world.a, f"Qual o preço da {world.a.data.product_name}?")
    await _assert_handoff(client, world.a, price, {"tool_failure"})
    order = await chat(client, world.a, f"pedido {world.a.data.order_id} {world.a.data.email}")
    await _assert_handoff(client, world.a, order, {"tool_failure"})
    _assert_clean(price, logs.text())
    _assert_clean(order, logs.text())
    assert not leaked(model_control.payloads(), NEEDLES)


async def test_embedding_failure_is_safe(client, world: World, monkeypatch, logs) -> None:
    async def broken(self, text):
        raise RuntimeError(LEAKY)

    monkeypatch.setattr(HashEmbeddingProvider, "embed_query", broken)
    # Unique wording so the shared query-embedding cache cannot serve a cached vector.
    reply = await chat(client, world.a, f"Política de trocas {uuid.uuid4().hex}?")
    await _assert_handoff(client, world.a, reply, {"tool_failure", "no_information"})
    _assert_clean(reply, logs.text())


async def test_vault_redis_down_fails_closed(client, world: World, chat_service, model_control):
    chat_service.vault_store = RedisVaultStore(
        Settings(app_env="test", redis_url="redis://127.0.0.1:1/0")
    )
    reply = await chat(client, world.a, f"pedido {world.a.data.order_id} {world.a.data.email}")
    await _assert_handoff(client, world.a, reply, {"privacy_guard"})
    assert world.a.data.tracking not in reply["reply"]
    assert not model_control.calls  # nothing may reach the model without a working vault


async def test_model_timeout_is_safe(client, world: World, chat_service, model_control, logs):
    chat_service.settings = chat_service.settings.model_copy(update={"model_timeout_seconds": 0.05})

    async def slow(call):
        await asyncio.sleep(2)
        return [assistant_message("nunca")]

    model_control.factory = scripted(ModelStep.respond(slow))
    reply = await chat(client, world.a, "Qual o horário de funcionamento?")
    await _assert_handoff(client, world.a, reply, {"tool_failure"})
    assert "nunca" not in reply["reply"]
    _assert_clean(reply, logs.text())


async def test_model_error_with_pii_message_is_safe(client, world: World, model_control, logs):
    model_control.factory = scripted(ModelStep.raise_error(RuntimeError(LEAKY)))
    reply = await chat(client, world.a, "Qual o horário de funcionamento?")
    await _assert_handoff(client, world.a, reply, {"tool_failure"})
    _assert_clean(reply, logs.text())


async def test_unexpected_crash_returns_generic_500(
    client, world: World, chat_service, monkeypatch, logs
) -> None:
    def crash(*args, **kwargs):
        raise RuntimeError(LEAKY)

    # Raised deep inside ChatService.handle_message, outside any of its own handlers.
    monkeypatch.setattr(LocalCommerceBackend, "__init__", crash)
    response = await client.post(
        "/v1/chat/messages", headers=world.a.headers, json={"message": "Oi"}
    )
    assert response.status_code == 500
    assert_safe_error(response)
    assert response.json()["error"] == "internal_error"
    assert not leaked(response.text + logs.text(), NEEDLES)


async def test_database_down_returns_generic_error(app, client, world: World, logs) -> None:
    dsn = "postgresql+asyncpg://leaky_user:SuperSecretPw@127.0.0.1:1/emporio"
    engine = create_async_engine(dsn)
    dead = async_sessionmaker(engine, expire_on_commit=False)

    async def _dead_session() -> AsyncIterator[AsyncSession]:
        async with dead() as s:
            yield s

    app.dependency_overrides[get_session] = _dead_session
    try:
        response = await client.post(
            "/v1/chat/messages", headers=world.a.headers, json={"message": "Oi"}
        )
    finally:
        await engine.dispose()
    assert response.status_code >= 500
    assert_safe_error(response)
    for needle in ("SuperSecretPw", "leaky_user", "127.0.0.1:1", world.a.key):
        assert needle not in response.text
        assert needle not in logs.text()


async def test_failure_paths_do_not_leak_restricted_values_to_logs(
    client, world: World, monkeypatch, logs
) -> None:
    async def broken(self, *args, **kwargs):
        raise RuntimeError(LEAKY)

    monkeypatch.setattr(LocalCommerceBackend, "get_order", broken)
    await chat(client, world.a, f"pedido {world.a.data.order_id} {world.a.data.email}")
    assert not leaked(logs.text(), restricted_values(world.a.data))


async def test_order_lookup_fails_closed_when_limiter_is_down(
    client, world: World, chat_service, logs
) -> None:
    from app.services.attempt_limiter_redis import RedisAttemptLimiter

    chat_service.limiter = RedisAttemptLimiter.from_settings(
        Settings(app_env="test", redis_url="redis://127.0.0.1:1/0")
    )
    reply = await chat(client, world.a, f"pedido {world.a.data.order_id} {world.a.data.email}")
    assert world.a.data.tracking not in reply["reply"], reply
    await _assert_handoff(client, world.a, reply, {"tool_failure"})
    _assert_clean(reply, logs.text())
