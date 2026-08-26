from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from aether_agent_memory.api.dependencies import enforce_scope, json_model, runtime_of
from aether_agent_memory.b3.application import b3_candidates_from_context
from aether_agent_memory.context import ContextRequest
from aether_agent_memory.integration import request_context_from_payload

router = APIRouter()


@router.post("/api/v1/context")
async def context(request: Request, body: ContextRequest) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    request_context = request_context_from_payload(body)
    result = await runtime.build_context(body, request_context)
    return json_model(result)


@router.post("/api/v1/b3/candidates")
async def b3_candidates(request: Request, body: ContextRequest) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    request_context = request_context_from_payload(body)
    context_result = await runtime.build_context(body, request_context)
    return b3_candidates_from_context(context_result)
