from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.runtime.request_context import Scope


class MemoryFactStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    SUPERSEDED = "SUPERSEDED"
    DELETED = "DELETED"


class ProjectionStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class Provenance(BaseModel):
    source_event_id: str | None = None
    source_message_id: str | None = None
    source_object_id: str | None = None
    derived_from_memory_id: str | None = None
    algorithm: str | None = None
    algorithm_version: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryRecord(BaseModel):
    memory_id: str
    scope: Scope
    memory_type: MemoryType
    content_ref: str | None = None
    status: MemoryFactStatus = MemoryFactStatus.ACTIVE
    embedding_status: ProjectionStatus = ProjectionStatus.PENDING
    compression_status: ProjectionStatus = ProjectionStatus.NOT_APPLICABLE
    vector_projection_status: ProjectionStatus = ProjectionStatus.PENDING
    scheduler_signal_status: ProjectionStatus = ProjectionStatus.PENDING
    provenance: Provenance = Field(default_factory=Provenance)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
