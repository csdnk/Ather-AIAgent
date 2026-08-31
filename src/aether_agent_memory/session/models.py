from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.scope import Scope


class SessionMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class SessionMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: uuid4().hex)
    role: SessionMessageRole
    content: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request_id: str | None = None
    trace_id: str | None = None
    used_context_uris: list[AetherUri] = Field(default_factory=list)
    used_skills: list[str] = Field(default_factory=list)


class SessionArchive(BaseModel):
    archive_id: str = Field(default_factory=lambda: uuid4().hex)
    messages: list[SessionMessage]
    abstract: str
    overview: str
    # Stable revision of this archive's authoritative snapshot. The enclosing
    # SessionRecord revision may continue changing as worker status is updated.
    source_revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extraction_status: str = "not_implemented"


class SessionExtractionWorkStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SessionExtractionWorkItem(BaseModel):
    work_id: str = Field(default_factory=lambda: uuid4().hex)
    scope: Scope
    archive_id: str
    status: SessionExtractionWorkStatus = SessionExtractionWorkStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    claimed_at: datetime | None = None
    lease_until: datetime | None = None

    @model_validator(mode="after")
    def _requires_full_scope(self) -> SessionExtractionWorkItem:
        if any(
            getattr(self.scope, field) is None
            for field in ("tenant_id", "user_id", "agent_id", "session_id")
        ):
            raise ValueError(
                "session extraction work requires tenant/user/agent/session scope"
            )
        return self


class SessionRecord(BaseModel):
    scope: Scope
    revision: int = Field(default=0, ge=0)
    messages: list[SessionMessage] = Field(default_factory=list)
    archives: list[SessionArchive] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _requires_full_scope(self) -> SessionRecord:
        if any(
            getattr(self.scope, field) is None
            for field in ("tenant_id", "user_id", "agent_id", "session_id")
        ):
            raise ValueError("session record requires tenant/user/agent/session scope")
        return self


class SessionCommitResult(BaseModel):
    session_id: str
    archive_id: str | None = None
    archived_messages: int = Field(default=0, ge=0)
    retained_messages: int = Field(default=0, ge=0)
    revision: int = Field(ge=0)
    status: str
    memory_extraction_status: str = "not_implemented"
    context_projection_status: str = "not_implemented"


class SessionConsolidationResult(BaseModel):
    session_id: str
    archive_id: str
    memory_id: str
    memory_ids: list[str] = Field(default_factory=list)
    status: str
    extraction_status: str = "succeeded"
    trace_id: str
