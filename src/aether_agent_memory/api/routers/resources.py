from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator

from aether_agent_memory.api.dependencies import enforce_scope, json_model, runtime_of
from aether_agent_memory.application.resource import ResourceRegistration
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.integration import request_context_from_payload

router = APIRouter()


class RegisterResourceRequest(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str
    session_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    resource_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = "documents"
    media_type: str = "text/plain"
    description: str = ""
    overview: str = ""
    content: str | None = None
    content_ref: str | None = None
    source: str = "api"
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _requires_content_source(self) -> RegisterResourceRequest:
        if not (self.content and self.content.strip()) and not self.content_ref:
            raise ValueError("resource requires content or content_ref")
        return self


class DeleteResourceRequest(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str
    session_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    category: str = "documents"


@router.post("/api/v1/context/resources")
async def register_resource(
    request: Request,
    body: RegisterResourceRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    registration = ResourceRegistration(
        resource_id=body.resource_id,
        name=body.name,
        scope=Scope(
            tenant_id=body.tenant_id,
            user_id=body.user_id,
            agent_id=body.agent_id,
            session_id=body.session_id,
        ),
        category=body.category,
        media_type=body.media_type,
        description=body.description,
        overview=body.overview,
        content=body.content,
        content_ref=body.content_ref,
        source=body.source,
        metadata=body.metadata,
    )
    resource = await runtime.register_resource(registration, context)
    return {"resource": json_model(resource)}


@router.post("/api/v1/context/resources/{resource_id}/delete")
async def delete_resource(
    request: Request,
    resource_id: str,
    body: DeleteResourceRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    result = await runtime.delete_resource(
        resource_id,
        context,
        category=body.category,
    )
    return {"deletion": json_model(result)}


__all__ = ["DeleteResourceRequest", "RegisterResourceRequest", "router"]
