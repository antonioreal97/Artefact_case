"""Provider adapters only: the Agents SDK retains ownership of the agent/tool loop."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from agents import Model, ModelSettings
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import (
    ModelResponse,
    TResponseInputItem,
    TResponseOutputItem,
    TResponseStreamEvent,
)
from agents.models.interface import ModelTracing
from agents.tool import FunctionTool, Tool
from agents.usage import Usage
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_prompt_param import ResponsePromptParam

from app.core.config import Settings


def split_model(model: str | None, *, embeddings: bool = False) -> tuple[str, str]:
    provider, separator, name = (model or "").partition(":")
    supported = {"openai", "ollama"} if embeddings else {"openai", "anthropic", "ollama"}
    if not separator or not name.strip() or provider not in supported:
        kind = "embedding" if embeddings else "chat"
        raise ValueError(
            f"Unsupported LangChain {kind} model; use {'/'.join(sorted(supported))}:model"
        )
    return provider, name


class _OwnedChatOpenAI(ChatOpenAI):
    """Factory marker: both HTTP clients were created here, never borrowed or cached."""


class _OwnedChatOllama(ChatOllama):
    """Factory marker: Ollama constructs fresh sync/async clients for each instance."""


def create_chat_model(settings: Settings) -> BaseChatModel:
    provider, name = split_model(settings.langchain_chat_model)
    if provider == "openai":
        return _OwnedChatOpenAI(
            model=name,
            api_key=settings.openai_api_key,
            timeout=settings.model_timeout_seconds,
            max_retries=0,
            # SDK defaults select the matching HTTP library (httpx/httpx2 by SDK version).
            # Explicit clients bypass langchain-openai's process-wide cached clients.
            http_client=DefaultHttpxClient(timeout=settings.model_timeout_seconds),
            http_async_client=DefaultAsyncHttpxClient(timeout=settings.model_timeout_seconds),
            use_responses_api=False,
            store=False,
            callbacks=[],
            verbose=False,
        )
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=name,
            api_key=settings.anthropic_api_key,
            timeout=settings.model_timeout_seconds,
            max_retries=0,
            callbacks=[],
            verbose=False,
        )
    return _OwnedChatOllama(
        model=name,
        base_url=settings.ollama_base_url,
        client_kwargs={"timeout": settings.model_timeout_seconds},
        callbacks=[],
        verbose=False,
    )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {
                "text",
                "input_text",
                "output_text",
            }:
                parts.append(str(block["text"]))
            else:
                raise ValueError("LangChain adapter supports text messages only")
        return "".join(parts)
    raise ValueError("LangChain adapter requires text content")


def to_messages(
    instructions: str | None, input: str | list[TResponseInputItem]
) -> list[BaseMessage]:
    messages: list[BaseMessage] = [SystemMessage(content=instructions)] if instructions else []
    if isinstance(input, str):
        return [*messages, HumanMessage(content=input)]
    for raw_item in input:
        item: dict[str, Any] = dict(raw_item)
        kind = item.get("type")
        if kind == "function_call":
            arguments = json.loads(str(item["arguments"]))
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object")
            call: ToolCall = {
                "name": str(item["name"]),
                "args": arguments,
                "id": str(item["call_id"]),
            }
            if messages and isinstance(messages[-1], AIMessage):
                messages[-1].tool_calls.append(call)
            else:
                messages.append(AIMessage(content="", tool_calls=[call]))
        elif kind == "function_call_output":
            messages.append(
                ToolMessage(content=_text(item["output"]), tool_call_id=str(item["call_id"]))
            )
        elif kind in (None, "message"):
            role = item.get("role")
            text = _text(item.get("content", ""))
            if role in ("system", "developer"):
                messages.append(SystemMessage(content=text))
            elif role == "user":
                messages.append(HumanMessage(content=text))
            elif role == "assistant":
                messages.append(AIMessage(content=text))
            else:
                raise ValueError("Unsupported message role in LangChain adapter")
        else:
            raise ValueError("Unsupported input item in LangChain adapter")
    return messages


class LangChainModel(Model):
    def __init__(self, model: BaseChatModel, *, provider: str = "") -> None:
        self.model = model
        self.provider = provider

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
        if previous_response_id or conversation_id or prompt:
            raise ValueError("Provider-hosted state is not supported")
        if handoffs or output_schema:
            raise ValueError("SDK handoffs and structured output are not supported by this adapter")
        schemas = []
        for tool in tools:
            if not isinstance(tool, FunctionTool):
                raise ValueError("Only local FunctionTool tools are supported")
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.params_json_schema,
                    },
                }
            )
        kwargs: dict[str, Any] = {}
        for key in ("temperature", "max_tokens", "top_p"):
            value = getattr(model_settings, key)
            if value is not None:
                kwargs[
                    "num_predict" if self.provider == "ollama" and key == "max_tokens" else key
                ] = value
        if self.provider == "ollama" and kwargs:
            kwargs = {"options": kwargs}
        binding: dict[str, Any] = {}
        if model_settings.tool_choice is not None:
            choice = model_settings.tool_choice
            if not isinstance(choice, str):
                raise ValueError("Hosted tool choices are not supported")
            if self.provider == "ollama":
                if choice != "auto":
                    raise ValueError("Ollama does not support forcing tool_choice")
            elif self.provider == "anthropic":
                binding["tool_choice"] = (
                    "any"
                    if choice == "required"
                    else {"type": "none"}
                    if choice == "none"
                    else choice
                )
            else:
                binding["tool_choice"] = choice
        if (
            self.provider in {"openai", "anthropic"}
            and model_settings.parallel_tool_calls is not None
        ):
            binding["parallel_tool_calls"] = model_settings.parallel_tool_calls
        runnable = self.model.bind_tools(schemas, **binding) if schemas else self.model
        if self.provider == "openai":
            kwargs["store"] = False
        response = await runnable.ainvoke(
            to_messages(system_instructions, input), config={"callbacks": []}, **kwargs
        )
        if not isinstance(response, AIMessage) or response.invalid_tool_calls:
            raise ValueError("Invalid chat model response")
        if model_settings.parallel_tool_calls is False and len(response.tool_calls) > 1:
            raise ValueError("Parallel tool calls are disabled")
        output: list[TResponseOutputItem] = []
        # Providers may mix text with reasoning/tool blocks; only public text is an answer.
        content = response.content
        text = (
            content
            if isinstance(content, str)
            else "".join(
                block if isinstance(block, str) else str(block.get("text", ""))
                for block in content
                if isinstance(block, str) or block.get("type") == "text"
            )
        )
        if text:
            output.append(
                ResponseOutputMessage(
                    id=f"msg_{uuid4().hex}",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
                )
            )
        for call in response.tool_calls:
            output.append(
                ResponseFunctionToolCall(
                    type="function_call",
                    name=call["name"],
                    arguments=json.dumps(call["args"]),
                    call_id=call.get("id") or f"call_{uuid4().hex}",
                )
            )
        usage: dict[str, Any] = dict(response.usage_metadata or {})
        return ModelResponse(
            output=output,
            response_id=None,
            usage=Usage(
                requests=1,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
        )

    def stream_response(
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
        raise NotImplementedError("LangChain streaming is unsupported; ChatService uses Runner.run")

    async def close(self) -> None:
        # Never infer ownership from provider name or close borrowed SDK roots.
        # Anthropic caches HTTP clients and offers no public injection fields; leave
        # those process-owned clients alone. Externally supplied models are borrowed too.
        if isinstance(self.model, _OwnedChatOpenAI):
            try:
                if self.model.http_async_client is not None:
                    await self.model.http_async_client.aclose()
            finally:
                if self.model.http_client is not None:
                    self.model.http_client.close()
        elif isinstance(self.model, _OwnedChatOllama):
            try:
                await self.model._async_client._client.aclose()
            finally:
                self.model._client._client.close()
