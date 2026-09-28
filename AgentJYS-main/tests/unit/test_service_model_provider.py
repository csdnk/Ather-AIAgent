"""HTTP protocol/limits only. These fixtures do not measure model quality."""

import asyncio
import json

import httpx
import pytest

from aether_agent_memory.remember.model_provider import ModelProvider, Supported
from aether_agent_memory.runtime.flows.config import LanguageModel

pytestmark = pytest.mark.asyncio

def response(value, finish="stop"):
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": finish, "message": {"content": json.dumps(value)}}]},
    )


async def test_json_adapter_authenticates_and_validates(monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "private-key")

    def handler(request):
        assert request.headers["authorization"] == "Bearer private-key"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "fixture-model"
        assert body["messages"][-1]["content"] == "untrusted input"
        return response({"supported": True, "reason": "fixture"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = ModelProvider(
            LanguageModel(model="fixture-model", api_key_env="TEST_MODEL_KEY"), client=client
        )
        assert await model.generate(Supported, [("user", "untrusted input")]) == {
            "supported": True,
            "reason": "fixture",
        }


@pytest.mark.parametrize("value,finish", [({}, "stop"), ({"supported": True}, "length")])
async def test_incomplete_or_invalid_model_output_is_rejected(value, finish):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response(value, finish))
    ) as client:
        model = ModelProvider(LanguageModel(model="test"), client=client)
        with pytest.raises(ValueError):
            await model.generate(Supported, [("user", "source")])


async def test_model_wait_and_response_size_are_bounded():
    async def slow(request):
        await asyncio.sleep(0.2)
        return response({"supported": True, "reason": "fixture"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        model = ModelProvider(LanguageModel(model="test", timeout_seconds=0.01), client=client)
        with pytest.raises(TimeoutError):
            await model.generate(Supported, [])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 2048))
    ) as client:
        model = ModelProvider(LanguageModel(model="test", max_response_bytes=1024), client=client)
        with pytest.raises(ValueError, match="limit"):
            await model.generate(Supported, [])


async def test_model_concurrency_is_limited():
    active = maximum = 0

    async def handler(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return response({"supported": True, "reason": "fixture"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = ModelProvider(LanguageModel(model="test", concurrency=2), client=client)
        await asyncio.gather(*(model.generate(Supported, []) for _ in range(8)))
        assert maximum == 2
