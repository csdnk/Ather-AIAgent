from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from aether_agent_memory.api.dependencies import enforce_scope, json_model, runtime_of
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.integration import request_context_from_payload

router = APIRouter()


@router.post("/api/v1/memory/events")
async def memory_events(request: Request, body: MemoryEvent) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    result = await runtime.write_memory(body, context)
    return json_model(result)
