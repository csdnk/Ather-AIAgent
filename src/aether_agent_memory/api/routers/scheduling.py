from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from aether_agent_memory.api.dependencies import json_model, runtime_of
from aether_agent_memory.b3 import ScheduleRequest
from aether_agent_memory.demo.service import record_schedule_result
from aether_agent_memory.integration import request_context_from_payload

router = APIRouter()


@router.post("/api/v1/schedules")
async def schedule(request: Request, body: ScheduleRequest) -> dict[str, Any]:
    runtime = runtime_of(request)
    context = request_context_from_payload(body)
    result = await runtime.schedule(body, context)
    payload = json_model(result)
    record_schedule_result("api-v1-schedules", payload)
    return payload
