from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from aether_agent_memory.api.dependencies import enforce_scope, json_model, runtime_of
from aether_agent_memory.integration import request_context_from_payload

router = APIRouter()


class ProjectionReconcileRequest(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str
    session_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None


@router.post("/api/v1/projections/reconcile")
async def reconcile_projections(
    request: Request,
    body: ProjectionReconcileRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    items = await runtime.reconcile_projection_work(
        context,
        session_only=body.session_id is not None,
    )
    return {
        "items": [json_model(item) for item in items],
        "request_id": context.request_id,
        "trace_id": context.trace_id,
    }


__all__ = ["ProjectionReconcileRequest", "router"]
