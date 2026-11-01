"""Non-optional privacy checks immediately before every provider request."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import replace

from agents import Model, ModelProvider, ModelSettings
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import ModelTracing
from agents.models.openai_provider import OpenAIProvider
from agents.tool import Tool
from openai import AsyncOpenAI
from openai.types.responses.response_prompt_param import ResponsePromptParam

from app.core.config import Settings
from app.services.privacy import PrivacyBoundaryError, scan_outbound


class PrivacyGuardedModel(Model):
    def __init__(self, inner: Model, allowlist: Iterable[str] = ()) -> None:
        self.inner = inner
        self.allowlist = tuple(allowlist)

    def _check(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
        model_settings: ModelSettings,
    ) -> ModelSettings:
        if previous_response_id or conversation_id or prompt:
            raise PrivacyBoundaryError("Provider-hosted state is not permitted")
        scan_outbound(
            {"instructions": system_instructions, "input": input}, allowlist=self.allowlist
        )
        # extra_body can override explicit OpenAI request fields. Do not accept it.
        if model_settings.extra_body:
            raise PrivacyBoundaryError("Provider body overrides are not permitted")
        return replace(model_settings, store=False)

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        settings = self._check(
            system_instructions,
            input,
            previous_response_id,
            conversation_id,
            prompt,
            model_settings,
        )
        return await self.inner.get_response(
            system_instructions,
            input,
            settings,
            tools,
            output_schema,
            handoffs,
            ModelTracing.DISABLED if tracing.is_disabled() else ModelTracing.ENABLED_WITHOUT_DATA,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        )

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        settings = self._check(
            system_instructions,
            input,
            previous_response_id,
            conversation_id,
            prompt,
            model_settings,
        )
        async for event in self.inner.stream_response(
            system_instructions,
            input,
            settings,
            tools,
            output_schema,
            handoffs,
            ModelTracing.DISABLED if tracing.is_disabled() else ModelTracing.ENABLED_WITHOUT_DATA,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        ):
            yield event

    async def close(self) -> None:
        await self.inner.close()


class GuardedModelProvider(ModelProvider):
    def __init__(self, inner: ModelProvider | Model, allowlist: Iterable[str] = ()) -> None:
        self.inner = inner
        self.allowlist = tuple(allowlist)

    def get_model(self, model_name: str | None) -> Model:
        model = (
            self.inner.get_model(model_name)
            if isinstance(self.inner, ModelProvider)
            else self.inner
        )
        return PrivacyGuardedModel(model, self.allowlist)

    async def aclose(self) -> None:
        if isinstance(self.inner, ModelProvider):
            await self.inner.aclose()
        else:
            await self.inner.close()


class _OpenAIProvider(OpenAIProvider):
    """Own the explicitly configured client so a per-turn provider closes it."""

    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        super().__init__(openai_client=client, use_responses=True, use_responses_websocket=False)
        self.client = client
        self.default_model = model

    def get_model(self, model_name: str | None) -> Model:
        return super().get_model(model_name or self.default_model)

    async def aclose(self) -> None:
        await super().aclose()
        await self.client.close()


def build_model_provider(settings: Settings, allowlist: Iterable[str]) -> GuardedModelProvider:
    if settings.llm_provider == "fake":
        if settings.app_env == "prod":
            raise ValueError("FakeCommerceModel is prohibited in production")
        from app.services.fake_model import FakeCommerceModel

        return GuardedModelProvider(FakeCommerceModel(), allowlist)
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value().strip():
        raise ValueError("OPENAI_API_KEY is required")
    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.model_timeout_seconds,
        max_retries=0,
    )
    return GuardedModelProvider(_OpenAIProvider(client, settings.openai_chat_model), allowlist)
