from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from aether_agent_memory.api.dependencies import (
    enforce_scope,
    json_model,
    json_response,
    runtime_of,
)
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


@router.get(
    "/api/v1/context/traces/{trace_id}",
    response_model=None,
)
async def retrieval_trace(
    request: Request,
    trace_id: str,
    tenant_id: str,
    user_id: str,
    agent_id: str,
    session_id: str | None = None,
) -> Response | dict[str, Any]:
    """Return one scope-authorized retrieval trace for debugging clients."""

    runtime = runtime_of(request)
    context = request_context_from_payload(
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": session_id,
        }
    )
    trace = await runtime.get_retrieval_trace(trace_id, context)
    if trace is None:
        return json_response(
            request,
            {"error": "retrieval trace not found", "trace_id": trace_id},
            status_code=404,
        )
    return json_model(trace)


@router.post("/api/v1/b3/candidates")
async def b3_candidates(request: Request, body: ContextRequest) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    request_context = request_context_from_payload(body)
    context_result = await runtime.build_context(body, request_context)
    return b3_candidates_from_context(context_result)
