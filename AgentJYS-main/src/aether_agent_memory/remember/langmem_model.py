"""Tool-capable LangChain model for the official LangMem execution path."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, PrivateAttr

from aether_agent_memory.runtime.flows.config import LanguageModel


class LangMemOutputTruncatedError(ValueError):
    """A bounded generation ended before all memory tool output was complete."""


class _LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, limit: int) -> None:
        self.stream, self.limit = stream, limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        total = 0
        async for part in self.stream:
            total += len(part)
            if total > self.limit:
                raise ValueError("model response exceeds configured limit")
            yield part

    async def aclose(self) -> None:
        await self.stream.aclose()


class _LimitedTransport(httpx.AsyncBaseTransport):
    def __init__(self, limit: int) -> None:
        self.transport = httpx.AsyncHTTPTransport()
        self.limit = limit

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.transport.handle_async_request(request)
        response.stream = _LimitedStream(response.stream, self.limit)
        return response

    async def aclose(self) -> None:
        await self.transport.aclose()


class LangMemToolReadiness(BaseModel):
    """Call this tool once with ready=true to verify the tool-calling deployment."""

    model_config = ConfigDict(extra="forbid")
    ready: Literal[True]


class LangMemChatModel(ChatOpenAI):
    """Use normal OpenAI tools with bounded async calls and no implicit retries."""

    _deployment: LanguageModel = PrivateAttr()
    _slots: asyncio.Semaphore = PrivateAttr()
    _owned_client: httpx.AsyncClient | None = PrivateAttr(default=None)
    _health_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
    _health_task: asyncio.Task[dict[str, object]] | None = PrivateAttr(default=None)
    _health_result: dict[str, object] | None = PrivateAttr(default=None)
    _health_checked_at: float = PrivateAttr(default=0)
    _closed: bool = PrivateAttr(default=False)

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        requested = kwargs.get("max_tokens", kwargs.get("max_completion_tokens"))
        limit = self._deployment.max_output_tokens
        if requested is not None:
            if not isinstance(requested, int) or requested <= 0:
                raise ValueError("invalid model output token limit")
            limit = min(limit, requested)
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # ChatOpenAI normalizes legacy max_tokens; retain the explicit deployment
        # setting for servers that implement only one of the two parameters.
        for name in ("max_tokens", "max_completion_tokens"):
            payload.pop(name, None)
        payload[self._deployment.token_limit_parameter] = limit
        if self._deployment.temperature is None:
            payload.pop("temperature", None)
        return payload

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise RuntimeError("LangMem deployment adapter requires async invocation")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self._closed:
            raise RuntimeError("LangMem deployment adapter is closed")
        async with asyncio.timeout(self._deployment.timeout_seconds), self._slots:
            result = await super()._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
        # Also bound injected clients used by tests and custom deployment transports.
        if len(result.model_dump_json().encode("utf-8")) > self._deployment.max_response_bytes:
            raise ValueError("model response exceeds configured limit")
        if len(result.generations) != 1:
            raise ValueError("LangMem requires exactly one model choice")
        generation = result.generations[0]
        finish_reason = (generation.generation_info or {}).get("finish_reason")
        if finish_reason in {"length", "max_tokens", "max_output_tokens", "max_completion_tokens"}:
            raise LangMemOutputTruncatedError("model output exceeded the generation token limit")
        if finish_reason not in {
            None,
            "stop",
            "tool_calls",
        }:
            raise ValueError("model output did not finish normally")
        if generation.message.additional_kwargs.get("refusal"):
            raise ValueError("model refused LangMem consolidation")
        if getattr(generation.message, "invalid_tool_calls", ()):
            raise ValueError("model returned invalid tool arguments")
        return result

    def checkpoint_identity(self) -> dict[str, Any]:
        config = self._deployment
        url = urlsplit(config.endpoint)
        endpoint = urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
        return {
            "provider": "openai_compatible_tools",
            "endpoint": endpoint.rstrip("/"),
            "model": config.model,
            "token_limit_parameter": config.token_limit_parameter,
            "max_output_tokens": config.max_output_tokens,
            "max_response_bytes": config.max_response_bytes,
            "temperature": config.temperature,
            "prompt_version": config.prompt_version,
        }

    async def aclose(self) -> None:
        self._closed = True
        if self._health_task is not None and not self._health_task.done():
            self._health_task.cancel()
            await asyncio.gather(self._health_task, return_exceptions=True)
        if self._owned_client is not None:
            await self._owned_client.aclose()

    async def health(self) -> dict[str, object]:
        async with self._health_lock:
            if self._closed:
                return {"state": "unavailable", "reason": "closed"}
            if self._health_result is not None and (
                time.monotonic() - self._health_checked_at < self._deployment.health_cache_seconds
            ):
                return dict(self._health_result)
            if self._health_task is None or self._health_task.done():
                self._health_task = asyncio.create_task(self._probe_health())
            task = self._health_task
        # A short caller deadline does not cancel the bounded shared provider call.
        return dict(await asyncio.shield(task))

    async def _probe_health(self) -> dict[str, object]:
        try:
            reply = await self.bind_tools(
                [LangMemToolReadiness],
                tool_choice="LangMemToolReadiness",
            ).ainvoke(
                [("user", "Deployment diagnostic: call LangMemToolReadiness with ready=true.")],
                max_tokens=self._deployment.health_max_output_tokens,
            )
            calls = getattr(reply, "tool_calls", ())
            if len(calls) != 1 or calls[0]["name"] != "LangMemToolReadiness":
                raise ValueError("tool_call_required")
            LangMemToolReadiness.model_validate(calls[0]["args"])
            result: dict[str, object] = {"state": "available"}
        except Exception as exc:
            result = {"state": "unavailable", "reason": type(exc).__name__}
        self._health_checked_at = time.monotonic()
        self._health_result = result
        return result


def create_langmem_chat_model(
    config: LanguageModel,
    *,
    client: httpx.AsyncClient | None = None,
) -> LangMemChatModel:
    """Build the real tool model; credentials are read only from the configured env."""
    owned = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=config.timeout_seconds,
            follow_redirects=False,
            transport=_LimitedTransport(config.max_response_bytes),
        )
    model = LangMemChatModel(
        model=config.model,
        base_url=config.endpoint.rstrip("/"),
        api_key=os.environ.get(config.api_key_env) or "local-no-key",
        timeout=config.timeout_seconds,
        max_retries=0,
        max_tokens=config.max_output_tokens,
        temperature=config.temperature,
        http_async_client=client,
        streaming=False,
        use_responses_api=False,
    )
    model._deployment = config
    model._slots = asyncio.Semaphore(config.concurrency)
    model._owned_client = client if owned else None
    return model
