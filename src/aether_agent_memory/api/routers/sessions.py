from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from aether_agent_memory.api.dependencies import enforce_scope, json_model, runtime_of
from aether_agent_memory.integration import request_context_from_payload
from aether_agent_memory.session import SessionMessage, SessionMessageRole

router = APIRouter()


class SessionScopeRequest(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str
    session_id: str
    request_id: str | None = None
    trace_id: str | None = None


class AppendSessionMessageRequest(SessionScopeRequest):
    role: SessionMessageRole
    content: str = Field(min_length=1)
    message_id: str | None = None
    used_context_uris: list[str] = Field(default_factory=list)
    used_skills: list[str] = Field(default_factory=list)


class CommitSessionRequest(SessionScopeRequest):
    keep_recent_count: int = Field(default=5, ge=0)


class ConsolidateSessionRequest(SessionScopeRequest):
    archive_id: str = Field(min_length=1)


@router.post("/api/v1/sessions/messages")
async def append_message(
    request: Request,
    body: AppendSessionMessageRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    message = SessionMessage(
        message_id=body.message_id or uuid4().hex,
        role=body.role,
        content=body.content,
        request_id=context.request_id,
        trace_id=context.trace_id,
        used_context_uris=body.used_context_uris,
        used_skills=body.used_skills,
    )
    record = await runtime.append_session_message(message, context)
    return {"session": json_model(record)}


@router.post("/api/v1/sessions/commit")
async def commit_session(
    request: Request,
    body: CommitSessionRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    result = await runtime.commit_session(
        context,
        keep_recent_count=body.keep_recent_count,
    )
    return json_model(result)


@router.post("/api/v1/sessions/current")
async def current_session(
    request: Request,
    body: SessionScopeRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    record = await runtime.get_session(context)
    return {"session": json_model(record) if record is not None else None}


@router.post("/api/v1/sessions/consolidate")
async def consolidate_session(
    request: Request,
    body: ConsolidateSessionRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    result = await runtime.consolidate_session(
        context,
        archive_id=body.archive_id,
    )
    return json_model(result)


__all__ = [
    "AppendSessionMessageRequest",
    "CommitSessionRequest",
    "ConsolidateSessionRequest",
    "SessionScopeRequest",
    "router",
]
