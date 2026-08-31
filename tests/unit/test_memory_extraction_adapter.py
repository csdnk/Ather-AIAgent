from __future__ import annotations

import json

import httpx
import pytest

from aether_agent_memory.adapters.memory_extraction import (
    HttpMemoryExtractionAdapter,
)
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.session.models import SessionArchive


def _archive() -> SessionArchive:
    return SessionArchive(
        archive_id="archive-1",
        messages=[],
        abstract="session abstract",
        overview="session overview",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_http_extraction_adapter_returns_typed_candidates_and_scope_payload() -> None:
    received: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert isinstance(payload, dict)
        received.update(payload)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": "The team selected the P3 architecture.",
                        "importance": 0.8,
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = HttpMemoryExtractionAdapter(
        "http://extractor.local/v1/extract",
        model="company-memory-v1",
        api_key="secret",
        client=client,
    )
    context = RequestContext.from_values(
        request_id="request-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )

    candidates = await adapter.extract(_archive(), context)
    await client.aclose()

    assert len(candidates) == 1
    assert candidates[0].content.startswith("The team")
    assert candidates[0].importance == pytest.approx(0.8)
    assert received["model"] == "company-memory-v1"
    assert received["scope"]["tenant_id"] == "tenant-1"  # type: ignore[index]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_http_extraction_adapter_rejects_malformed_or_oversized_response() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"candidates": [{"bad": "x"}]})
        )
    )
    adapter = HttpMemoryExtractionAdapter(
        "http://extractor.local/v1/extract",
        client=client,
        max_candidates=1,
    )
    context = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )

    with pytest.raises(ValueError):
        await adapter.extract(_archive(), context)

    await client.aclose()
