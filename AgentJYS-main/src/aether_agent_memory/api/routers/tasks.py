from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from aether_agent_memory.api.dependencies import (
    enforce_scope,
    json_response,
    runtime_of,
)
from aether_agent_memory.api.mappers import (
    long_memory_submission_to_v1,
    memory_search_result_to_v1,
    task_status_record_to_v1,
)
from aether_agent_memory.integration import request_context_from_payload
from aether_agent_memory.integration.schemas import LongTextRequest, SearchRequest

router = APIRouter()


@router.get("/api/v1/b2/tasks/{task_id}", response_model=None)
async def get_task(request: Request, task_id: str) -> Response | dict[str, Any]:
    runtime = runtime_of(request)
    record = await runtime.get_task(
        task_id, request_context_from_payload({"task_id": task_id})
    )
    if record is None:
        return json_response(
            request,
            {"error": "task not found", "task_id": task_id},
            status_code=404,
        )
    return task_status_record_to_v1(record)


@router.post("/api/v1/b2/long-text", response_model=None)
async def long_text(request: Request, body: LongTextRequest) -> Response | dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    submission = await runtime.submit_long_memory(
        text=body.text,
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        agent_id=body.agent_id,
        session_id=body.session_id,
        source_id=body.source_id,
        object_id=body.object_id,
        content_ref=body.content_ref,
        context=context,
    )
    return json_response(request, long_memory_submission_to_v1(submission), status_code=202)


@router.post("/api/v1/b2/search")
async def b2_search(request: Request, body: SearchRequest) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    result = await runtime.search_memory(
        query=body.query,
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        agent_id=body.agent_id,
        limit=body.limit,
        context=context,
    )
    return memory_search_result_to_v1(result)
