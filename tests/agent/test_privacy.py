from __future__ import annotations

import json
import uuid

import pytest
from agents import ModelSettings
from agents.models.interface import ModelTracing
from agents.testing import ScriptedModel, assistant_message

from app.core.config import Settings
from app.services.model_gateway import PrivacyGuardedModel, build_model_provider
from app.services.privacy import PrivacyBoundaryError, redact_user_text, scan_outbound
from app.services.privacy_vault import PrivacyVault
from app.services.vault_store import InMemoryVaultStore


@pytest.mark.parametrize(
    ("text", "expected", "kind", "value"),
    [
        ("pedido 8", "pedido [[ORDER_1]]", "ORDER", "8"),
        ("pedido nº 8", "pedido nº [[ORDER_1]]", "ORDER", "8"),
        ("#8", "#[[ORDER_1]]", "ORDER", "8"),
        ("order 8", "order [[ORDER_1]]", "ORDER", "8"),
        ("a@example.test", "[[EMAIL_1]]", "EMAIL", "a@example.test"),
        ("(11) 99999-8888", "[[PHONE_1]]", "PHONE", "(11) 99999-8888"),
        ("+55 11 99999-8888", "[[PHONE_1]]", "PHONE", "+55 11 99999-8888"),
        ("11999998888", "[[PHONE_1]]", "PHONE", "11999998888"),
        ("BR123456789BR", "[[TRACKING_1]]", "TRACKING", "BR123456789BR"),
        ("123.456.789-01", "[[CPF_1]]", "CPF", "123.456.789-01"),
        ("CPF 12345678901", "CPF [[CPF_1]]", "CPF", "12345678901"),
        ("01234-567", "[[CEP_1]]", "CEP", "01234-567"),
        ("sk-abcdefghijklmnop", "[[SECRET_1]]", "SECRET", "sk-abcdefghijklmnop"),
        ("senha=segredo", "senha=[[SECRET_1]]", "SECRET", "segredo"),
    ],
)
def test_redaction(text, expected, kind, value):
    vault = PrivacyVault()
    assert redact_user_text(text, vault) == expected
    assert vault.resolve(f"[[{kind}_1]]") == (kind, value)
    assert vault.reveal(expected) == text
    scan_outbound(expected, allowlist=[])


def test_known_names_and_determinism():
    vault = PrivacyVault()
    assert redact_user_text("Ana Teste", vault) == "Ana Teste"  # documented free-text limit
    assert (
        redact_user_text("Ana Teste", vault, known_restricted_values=["Ana Teste"]) == "[[NAME_1]]"
    )
    assert (
        redact_user_text("pedido 8 e pedido 8", vault) == "pedido [[ORDER_1]] e pedido [[ORDER_1]]"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "a@example.test",
        "(11) 99999-8888",
        "pedido nº 8",
        "#8",
        "BR123456789BR",
        {"order_id": 8},
        {"customer_name": "Ana Teste"},
        {"tracking_code": "BR123456789BR"},
        {"type": "function_call_output", "output": json.dumps({"order_id": 8})},
    ],
)
async def test_gateway_blocks_before_inner_call(payload):
    inner = ScriptedModel([[assistant_message("ok")]])
    guarded = PrivacyGuardedModel(inner)
    with pytest.raises(PrivacyBoundaryError):
        await guarded.get_response(
            "Política",
            [{"role": "user", "content": json.dumps(payload)}],
            ModelSettings(store=True),
            [],
            None,
            [],
            ModelTracing.ENABLED,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        )
    assert not inner.calls


async def test_gateway_allows_tokens_store_contact_and_forces_controls():
    inner = ScriptedModel([[assistant_message("ok")]])
    guarded = PrivacyGuardedModel(inner, ["(67) 3341-4444", "loja@example.test"])
    await guarded.get_response(
        "Contato: (67) 3341 - 4444",
        [
            {"role": "user", "content": "pedido [[ORDER_1]]"},
            {
                "type": "function_call_output",
                "call_id": "call-one",
                "output": json.dumps(
                    {
                        "email": "[[EMAIL_1]]",
                        "tracking_code": "[[TRACKING_1]]",
                        "store": "loja@example.test",
                    }
                ),
            },
        ],
        ModelSettings(store=True),
        [],
        None,
        [],
        ModelTracing.ENABLED,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    )
    assert inner.calls[0].model_settings.store is False
    assert inner.calls[0].tracing == ModelTracing.ENABLED_WITHOUT_DATA
    assert inner.calls[0].conversation_id is None


async def test_stream_boundary():
    inner = ScriptedModel([[assistant_message("ok")]])
    guarded = PrivacyGuardedModel(inner)
    with pytest.raises(PrivacyBoundaryError):
        async for _ in guarded.stream_response(
            "a@example.test",
            "Olá",
            ModelSettings(),
            [],
            None,
            [],
            ModelTracing.DISABLED,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        ):
            pass
    assert not inner.calls
    events = [
        event
        async for event in guarded.stream_response(
            "Política",
            "Olá",
            ModelSettings(store=True),
            [],
            None,
            [],
            ModelTracing.DISABLED,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        )
    ]
    assert events
    assert inner.calls[0].model_settings.store is False


def test_allowlist_is_not_substring_bypass():
    with pytest.raises(PrivacyBoundaryError):
        scan_outbound("evil.loja@example.test", allowlist=["loja@example.test"])


async def test_vault_store_isolation_and_copy():
    store = InMemoryVaultStore()
    tenant, other, conversation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vault = PrivacyVault()
    vault.tokenize("ORDER", "8")
    await store.save(tenant, conversation, vault)
    vault.tokenize("ORDER", "9")
    loaded = await store.load(tenant, conversation)
    assert len(loaded.values) == 1
    assert not (await store.load(other, conversation)).values


def test_provider_requires_explicit_key_and_refuses_fake_in_prod():
    settings = Settings(llm_provider="openai", openai_api_key=None)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_model_provider(settings, [])
    settings = Settings(llm_provider="fake").model_copy(update={"app_env": "prod"})
    with pytest.raises(ValueError, match="production"):
        build_model_provider(settings, [])


@pytest.mark.parametrize(
    "options",
    [
        {"previous_response_id": "server-state"},
        {"conversation_id": "server-state"},
        {"prompt": {"id": "hosted-prompt"}},
    ],
)
async def test_provider_hosted_state_is_blocked(options):
    inner = ScriptedModel()
    guarded = PrivacyGuardedModel(inner)
    params = {"previous_response_id": None, "conversation_id": None, "prompt": None, **options}
    with pytest.raises(PrivacyBoundaryError):
        await guarded.get_response(
            "Política",
            "Olá",
            ModelSettings(),
            [],
            None,
            [],
            ModelTracing.DISABLED,
            **params,
        )
    assert not inner.calls


async def test_extra_body_cannot_override_store():
    guarded = PrivacyGuardedModel(ScriptedModel())
    with pytest.raises(PrivacyBoundaryError):
        await guarded.get_response(
            "Política",
            "Olá",
            ModelSettings(extra_body={"store": True}),
            [],
            None,
            [],
            ModelTracing.DISABLED,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        )


@pytest.mark.integration
async def test_redis_roundtrip_ttl_and_corrupt_state():
    from app.services.vault_store import VAULT_TTL_SECONDS, RedisVaultStore, VaultStoreUnavailable

    store = RedisVaultStore(Settings())
    tenant, conversation = uuid.uuid4(), uuid.uuid4()
    key = f"vault:{tenant}:{conversation}"
    try:
        vault = PrivacyVault()
        vault.tokenize("EMAIL", "ana@example.test")
        await store.save(tenant, conversation, vault)
        assert (await store.load(tenant, conversation)).values == vault.values
        assert VAULT_TTL_SECONDS - 5 <= await store.redis.ttl(key) <= VAULT_TTL_SECONDS
        await store.redis.set(key, "invalid", ex=10)
        with pytest.raises(VaultStoreUnavailable):
            await store.load(tenant, conversation)
    finally:
        await store.redis.delete(key)
        await store.aclose()
