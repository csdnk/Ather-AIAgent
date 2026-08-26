from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from aether_p4_simulator.client import P3MemoryClient
from aether_p4_simulator.models import (
    CreateSessionRequest,
    SendMessageRequest,
    SubmitDocumentRequest,
)
from aether_p4_simulator.service import P4SimulatorService


@pytest.mark.unit
async def test_p3_client_uses_only_frozen_http_endpoints() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = None
        if request.content:
            raw = request.read()
            payload = json.loads(raw)
        calls.append((request.method, request.url.path, payload))
        if request.url.path == "/health":
            return httpx.Response(200, json={"runtime_health": {"ready": True}})
        if request.url.path == "/api/v1/context":
            return httpx.Response(200, json={"memories": [], "memory_refs": []})
        return httpx.Response(200, json={"id": "memory-1"})

    client = P3MemoryClient(
        "http://p3.test",
        transport=httpx.MockTransport(handler),
    )
    await client.health()
    await client.build_context(
        tenant_id="tenant",
        user_id="user",
        agent_id="agent",
        session_id="session",
        query="hello",
        max_tokens=512,
        request_id="request-1",
        trace_id="trace-1",
    )
    await client.write_memory(
        tenant_id="tenant",
        user_id="user",
        agent_id="agent",
        session_id="session",
        content="hello",
        event_type="after_turn",
        source="agent",
        metadata={},
        request_id="request-2",
        trace_id="trace-1",
    )

    assert [(method, path) for method, path, _ in calls] == [
        ("GET", "/health"),
        ("POST", "/api/v1/context"),
        ("POST", "/api/v1/memory/events"),
    ]
    assert calls[1][2]["tenant_id"] == "tenant"
    assert calls[2][2]["idempotency_key"] == "p4-memory-request-2"


class FakeP3Client:
    base_url = "http://p3.test"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def health(self) -> dict[str, Any]:
        return {"runtime_health": {"ready": True}}

    async def build_context(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append("context")
        return {
            "status": "ok",
            "complete": True,
            "trace_id": "trace-1",
            "memory_refs": ["memory-old"],
            "memories": [{"id": "memory-old", "content": "The project uses P3 memory."}],
        }

    async def write_memory(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(f"memory:{kwargs['event_type']}")
        return {"id": "memory-new", "type": "semantic"}

    async def submit_long_text(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append("document")
        return {"task_id": "task-1", "state": "PENDING"}

    async def get_task(self, task_id: str) -> dict[str, Any]:
        self.calls.append("task")
        return {"task_id": task_id, "state": "SUCCEEDED"}


@pytest.mark.unit
async def test_p4_turn_reads_context_then_writes_durable_memory() -> None:
    p3 = FakeP3Client()
    service = P4SimulatorService(p3)
    session = service.create_session(
        CreateSessionRequest(
            agent_id="company-assistant",
            tenant_id="tenant",
            user_id="user",
            session_id="session-1",
        )
    )

    result = await service.send_message(
        session.session_id,
        SendMessageRequest(content="Remember our P3 design", durable_memory=True),
    )

    assert p3.calls == ["context", "memory:user_memory"]
    assert len(result.session.messages) == 2
    assert result.user_message.memory_id == "memory-new"
    assert result.assistant_message.context_memory_refs == ["memory-old"]
    assert "P3 returned 1 relevant memories" in result.assistant_message.content


@pytest.mark.unit
async def test_p4_document_flow_delegates_to_p3_task_api() -> None:
    p3 = FakeP3Client()
    service = P4SimulatorService(p3)
    session = service.create_session(CreateSessionRequest(agent_id="company-assistant"))

    submitted = await service.submit_document(
        session.session_id,
        SubmitDocumentRequest(source_id="note.txt", text="A sufficiently real document."),
    )
    completed = await service.get_task(str(submitted["task_id"]))

    assert submitted["state"] == "PENDING"
    assert completed["state"] == "SUCCEEDED"
    assert p3.calls == ["document", "task"]
