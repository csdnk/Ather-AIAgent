from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from aether_agent_memory.api.dependencies import enforce_scope, json_model, runtime_of
from aether_agent_memory.application.skill import SkillRegistration
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.integration import request_context_from_payload

router = APIRouter()


class RegisterSkillRequest(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str
    session_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    skill_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    instructions: str = ""
    version: str = Field(default="1", min_length=1)
    enabled: bool = True
    source: str = "api"
    metadata: dict[str, str] = Field(default_factory=dict)


class DeleteSkillRequest(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str
    session_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None


@router.post("/api/v1/context/skills")
async def register_skill(
    request: Request,
    body: RegisterSkillRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    skill = await runtime.register_skill(
        SkillRegistration(
            skill_id=body.skill_id,
            name=body.name,
            description=body.description,
            instructions=body.instructions,
            scope=Scope(
                tenant_id=body.tenant_id,
                user_id=body.user_id,
                agent_id=body.agent_id,
                session_id=body.session_id,
            ),
            version=body.version,
            enabled=body.enabled,
            source=body.source,
            metadata=body.metadata,
        ),
        context,
    )
    return {"skill": json_model(skill)}


@router.post("/api/v1/context/skills/{skill_id}/delete")
async def delete_skill(
    request: Request,
    skill_id: str,
    body: DeleteSkillRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    result = await runtime.delete_skill(skill_id, context)
    return {"deletion": json_model(result)}


__all__ = ["DeleteSkillRequest", "RegisterSkillRequest", "router"]
