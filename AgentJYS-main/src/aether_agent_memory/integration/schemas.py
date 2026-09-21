from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RuntimeEnvelope(BaseModel):
    request_id: str
    trace_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False


class LongTextRequest(BaseModel):
    """Northbound request for the asynchronous long-text memory task."""

    text: str
    tenant_id: str
    user_id: str
    agent_id: str
    session_id: str
    source_id: str
    request_id: str | None = None
    trace_id: str | None = None
    object_id: str | None = None
    content_ref: str | None = None
    idempotency_key: str | None = None


class SearchRequest(BaseModel):
    """Northbound request for long-term vector search."""

    query: str
    tenant_id: str
    user_id: str
    agent_id: str
    limit: int = Field(default=5, ge=1, le=100)
    session_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    idempotency_key: str | None = None
