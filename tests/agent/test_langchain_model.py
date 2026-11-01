"""Offline adapter contracts: no real model, embedding service or tracer is called."""

from __future__ import annotations

import asyncio
import json

import pytest
from agents import ModelSettings, function_tool
from agents.models.interface import ModelTracing
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, SecretStr, ValidationError

from app.agents.guardrails import SAFE_FALLBACK
from app.core.config import Settings
from app.services.langchain_model import LangChainModel, create_chat_model, to_messages
from app.services.model_gateway import PrivacyGuardedModel, build_model_provider
from app.services.privacy import PrivacyBoundaryError
from tests.agent.test_chat import commerce as commerce_fixture
from tests.agent.test_chat import make_service, turn

commerce = commerce_fixture


class RecordingChatModel(BaseChatModel):
    responses: list[AIMessage] = Field(default_factory=list)
    calls: list = Field(default_factory=list)
    schemas: list = Field(default_factory=list)
    bindings: dict = Field(default_factory=dict)
    handler_counts: list[int] = Field(default_factory=list)
    delay: float = 0
    fail: bool = False

    @property
    def _llm_type(self):
        return "offline-recording"

    def bind_tools(self, tools, **kwargs):
        self.schemas = tools
        self.bindings = kwargs
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("Only async invocation is expected")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append((messages, kwargs))
        self.handler_counts.append(len(run_manager.handlers))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("private provider diagnostic must not escape")
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


def request(model, *, input="Olá", tools=None, settings=None, **overrides):
    args = dict(
        system_instructions="Seja útil.",
        input=input,
        model_settings=settings or ModelSettings(),
        tools=tools or [],
        output_schema=None,
        handoffs=[],
        tracing=ModelTracing.DISABLED,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    )
    args.update(overrides)
    return model.get_response(**args)


@function_tool
async def price(product_id: int) -> str:
    """Look up a public price."""
    return "10"


async def test_message_tool_conversion_usage_and_settings():
    fake = RecordingChatModel(
        responses=[
            AIMessage(
                content=[{"type": "text", "text": "Consultando."}],
                tool_calls=[{"name": "price", "args": {"product_id": 1}, "id": "stable-call"}],
                usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
            )
        ]
    )
    model = LangChainModel(fake, provider="openai")
    response = await request(
        model,
        tools=[price],
        settings=ModelSettings(
            temperature=0.2,
            max_tokens=42,
            tool_choice="required",
            parallel_tool_calls=False,
            store=True,
        ),
    )
    assert response.output[0].content[0].text == "Consultando."
    call = response.output[1]
    assert (call.name, call.call_id, json.loads(call.arguments)) == (
        "price",
        "stable-call",
        {"product_id": 1},
    )
    assert (
        response.usage.input_tokens,
        response.usage.output_tokens,
        response.usage.total_tokens,
    ) == (7, 3, 10)
    messages, kwargs = fake.calls[0]
    assert isinstance(messages[0], SystemMessage) and isinstance(messages[1], HumanMessage)
    assert kwargs == {"temperature": 0.2, "max_tokens": 42, "store": False}
    assert fake.schemas[0]["function"]["parameters"] == price.params_json_schema
    assert fake.bindings == {"tool_choice": "required", "parallel_tool_calls": False}
    assert fake.handler_counts == [0] and fake.callbacks is None
    inputs = [item.model_dump(exclude_none=True) for item in response.output]
    inputs += [{"type": "function_call_output", "call_id": call.call_id, "output": "10"}]
    converted = to_messages(None, inputs)
    assert len(converted) == 2
    assert converted[0].content == "Consultando."
    assert converted[0].tool_calls[0]["id"] == "stable-call"
    assert isinstance(converted[1], ToolMessage) and converted[1].tool_call_id == "stable-call"


async def test_missing_call_ids_are_unique_and_survive_roundtrip():
    fake = RecordingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "price", "args": {"product_id": 1}, "id": None},
                    {"name": "price", "args": {"product_id": 2}, "id": None},
                ],
            )
        ]
    )
    response = await request(LangChainModel(fake))
    ids = [item.call_id for item in response.output]
    assert len(set(ids)) == 2 and all(ids)
    messages = to_messages(None, [item.model_dump(exclude_none=True) for item in response.output])
    assert [call["id"] for call in messages[0].tool_calls] == ids
    assert response.usage.total_tokens == 0


@pytest.mark.parametrize("payload", ["ana@example.test", "11999998888", "BR123456789BR"])
async def test_privacy_blocks_before_langchain_invocation(payload):
    fake = RecordingChatModel()
    with pytest.raises(PrivacyBoundaryError):
        await request(PrivacyGuardedModel(LangChainModel(fake)), input=payload)
    assert fake.calls == []


@pytest.mark.parametrize(
    "provider,model",
    [("llm_provider", "langchain_chat_model"), ("embedding_provider", "langchain_embedding_model")],
)
@pytest.mark.parametrize("value", [None, "", "  "])
def test_model_setting_required(provider, model, value):
    with pytest.raises(ValidationError, match="is required"):
        Settings(**{provider: "langchain", model: value})


@pytest.mark.parametrize("name", ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"])
@pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
def test_tracing_env_fails_closed(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match="must be OFF"):
        Settings()


def test_tracing_dotenv_fails_closed(tmp_path):
    env = tmp_path / "trace.env"
    env.write_text("LANGSMITH_TRACING=true\n")
    with pytest.raises(ValidationError, match="must be OFF"):
        Settings(_env_file=env)


@pytest.mark.parametrize("name", ["google:example", "openai:", "gpt-5-mini"])
def test_unknown_factory_fails(name):
    with pytest.raises(ValueError, match="Unsupported LangChain chat model"):
        create_chat_model(Settings(llm_provider="langchain", langchain_chat_model=name))


async def test_factory_storage_tracing_and_clients_offline():
    for provider, name in [
        ("openai", "gpt-5-mini"),
        ("anthropic", "claude-test"),
        ("ollama", "local-test"),
    ]:
        model = create_chat_model(
            Settings(
                langchain_chat_model=f"{provider}:{name}",
                openai_api_key=SecretStr("offline-test"),
                anthropic_api_key=SecretStr("offline-test"),
            )
        )
        assert model.callbacks == [] and not model.verbose
        if provider == "openai":
            assert model.store is False and model.use_responses_api is False
        await LangChainModel(model, provider=provider).close()


def test_build_provider_always_wraps(monkeypatch):
    fake = RecordingChatModel()
    monkeypatch.setattr("app.services.langchain_model.create_chat_model", lambda settings: fake)
    provider = build_model_provider(
        Settings(llm_provider="langchain", langchain_chat_model="openai:test"), []
    )
    guarded = provider.get_model("ignored-sdk-native-default")
    assert isinstance(guarded, PrivacyGuardedModel)
    assert isinstance(guarded.inner, LangChainModel) and guarded.inner.model is fake


@pytest.mark.integration
async def test_full_chat_service_tool_turn(session_factory, session, commerce):
    fake = RecordingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "get_price", "args": {"product_id": 1}, "id": "price-call"}],
            ),
            AIMessage(content="Violão Aurora: R$ 1.234,50."),
        ]
    )
    service, _ = make_service(session_factory, LangChainModel(fake))
    reply = await turn(service, session, commerce, "Qual o preço do Violão Aurora?")
    assert reply.grounded and "get_price" in reply.tools_used
    assert "R$ 1.234,50" in reply.reply
    assert len(fake.calls) == 2 and fake.handler_counts == [0, 0]
    tool_outputs = [message for message in fake.calls[1][0] if isinstance(message, ToolMessage)]
    assert tool_outputs[0].tool_call_id == "price-call"
    assert json.loads(tool_outputs[0].content)["ok"] is True


@pytest.mark.integration
@pytest.mark.parametrize("slow", [False, True])
async def test_model_error_or_timeout_safe_handoff(session_factory, session, commerce, slow):
    fake = RecordingChatModel(delay=1 if slow else 0, fail=not slow)
    service, _ = make_service(session_factory, LangChainModel(fake), model_timeout_seconds=0.05)
    reply = await turn(service, session, commerce, "Qual o preço?")
    assert SAFE_FALLBACK in reply.reply and reply.handoff.reason == "tool_failure"
    assert "private provider diagnostic" not in reply.reply
    assert len(fake.calls) == 1


def test_streaming_explicitly_unsupported():
    with pytest.raises(NotImplementedError, match=r"Runner\.run"):
        LangChainModel(RecordingChatModel()).stream_response(
            None,
            "hello",
            ModelSettings(),
            [],
            None,
            [],
            ModelTracing.DISABLED,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        )


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("ollama", {"options": {"temperature": 0.3, "num_predict": 20}}),
        ("anthropic", {"temperature": 0.3, "max_tokens": 20}),
    ],
)
async def test_provider_specific_settings(provider, expected):
    fake = RecordingChatModel(responses=[AIMessage(content="Olá")])
    await request(
        LangChainModel(fake, provider=provider),
        tools=[price],
        settings=ModelSettings(
            temperature=0.3,
            max_tokens=20,
            tool_choice="auto",
            parallel_tool_calls=False,
        ),
    )
    assert fake.calls[0][1] == expected


@pytest.mark.parametrize(
    "items",
    [
        [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_image", "image_url": "x"}],
            }
        ],
        [{"type": "item_reference", "id": "hosted"}],
    ],
)
def test_unsupported_input_fails_closed(items):
    with pytest.raises(ValueError, match=r"supports text|Unsupported input"):
        to_messages(None, items)


async def test_hosted_tools_rejected_before_model():
    from agents import WebSearchTool

    fake = RecordingChatModel()
    with pytest.raises(ValueError, match="Only local FunctionTool"):
        await request(LangChainModel(fake), tools=[WebSearchTool()])
    assert not fake.calls


async def test_parallel_tools_fail_when_disabled():
    fake = RecordingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "price", "args": {"product_id": 1}, "id": "one"},
                    {"name": "price", "args": {"product_id": 2}, "id": "two"},
                ],
            )
        ]
    )
    with pytest.raises(ValueError, match="Parallel tool calls are disabled"):
        await request(
            LangChainModel(fake, provider="ollama"),
            settings=ModelSettings(parallel_tool_calls=False),
        )


@pytest.mark.parametrize("name", ["langsmith_tracing", "langchain_tracing_v2"])
def test_tracing_forbidden_in_prod(name):
    with pytest.raises(ValidationError, match="must be OFF"):
        Settings(app_env="prod", **{name: True})


@pytest.fixture
def offline_chat_transport(monkeypatch):
    """Intercept HTTP client construction, retaining the real LangChain/OpenAI stack."""
    import socket

    from langchain_openai._compat import httpx

    def forbid_network(*args, **kwargs):
        raise AssertionError("Network forbidden in offline client lifecycle tests")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-offline",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-5-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Olá!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            },
        )

    transport = httpx.MockTransport(respond)
    for cls in (httpx.Client, httpx.AsyncClient):
        original = cls.__init__

        def initialize(self, *args, _original=original, **kwargs):
            kwargs.update(transport=transport, trust_env=False)
            _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, "__init__", initialize)
    return requests


@pytest.mark.parametrize("overlapping", [False, True])
async def test_real_openai_survives_consecutive_adapter_closes(offline_chat_transport, overlapping):
    settings = Settings(
        langchain_chat_model="openai:gpt-5-mini",
        openai_api_key=SecretStr("offline-test"),
        # Isolate the old integration's cache from other tests when reproducing the bug.
        model_timeout_seconds=17 if overlapping else 19,
    )

    def build():
        return LangChainModel(create_chat_model(settings), provider="openai")

    first = build()
    second = build() if overlapping else None
    try:
        assert (await request(first)).output[0].content[0].text == "Olá!"
        await first.close()
        assert first.model.http_client.is_closed
        assert first.model.http_async_client.is_closed
        second = second or build()
        assert first.model.http_client is not second.model.http_client
        assert first.model.http_async_client is not second.model.http_async_client
        assert second.model.http_client.timeout.read == settings.model_timeout_seconds
        assert second.model.http_async_client.timeout.read == settings.model_timeout_seconds
        assert (await request(second)).output[0].content[0].text == "Olá!"
        assert len(offline_chat_transport) == 2
    finally:
        await first.close()
        if second is not None:
            await second.close()


async def test_adapter_does_not_close_borrowed_openai_clients(offline_chat_transport):
    from langchain_openai import ChatOpenAI
    from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

    sync = DefaultHttpxClient()
    async_client = DefaultAsyncHttpxClient()
    try:
        model = ChatOpenAI(
            model="gpt-5-mini",
            api_key=SecretStr("offline-test"),
            http_client=sync,
            http_async_client=async_client,
            use_responses_api=False,
            max_retries=0,
        )
        first = LangChainModel(model, provider="openai")
        second = LangChainModel(model, provider="openai")
        await first.close()
        assert not sync.is_closed and not async_client.is_closed
        assert (await request(second)).output[0].content[0].text == "Olá!"
        await second.close()
        assert not sync.is_closed and not async_client.is_closed
        assert len(offline_chat_transport) == 1
    finally:
        await async_client.aclose()
        sync.close()


async def test_anthropic_shared_clients_remain_open():
    settings = Settings(
        langchain_chat_model="anthropic:claude-test",
        anthropic_api_key=SecretStr("offline-test"),
    )
    first, second = create_chat_model(settings), create_chat_model(settings)
    sync = first._client._client
    async_client = first._async_client._client
    assert second._client._client is sync
    assert second._async_client._client is async_client
    await LangChainModel(first, provider="anthropic").close()
    await LangChainModel(second, provider="anthropic").close()
    assert not sync.is_closed and not async_client.is_closed


async def test_ollama_factory_closes_only_its_own_clients():
    settings = Settings(langchain_chat_model="ollama:local-test")
    first, second = create_chat_model(settings), create_chat_model(settings)
    adapter = LangChainModel(first, provider="ollama")
    try:
        await adapter.close()
        await adapter.close()
        assert first._client._client.is_closed and first._async_client._client.is_closed
        assert not second._client._client.is_closed and not second._async_client._client.is_closed
    finally:
        await LangChainModel(second, provider="ollama").close()
