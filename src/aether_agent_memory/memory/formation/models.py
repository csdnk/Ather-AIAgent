from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from aether_agent_memory.memory.models import ProjectionStatus


class FormationStatus(StrEnum):
    FORMATION_PENDING = "FORMATION_PENDING"
    FORMATION_PROCESSING = "FORMATION_PROCESSING"
    FORMATION_SUCCEEDED = "FORMATION_SUCCEEDED"
    FORMATION_FAILED = "FORMATION_FAILED"


class MemoryFormationResult(BaseModel):
    memory_id: str | None = None
    task_id: str | None = None
    status: FormationStatus
    compression_status: ProjectionStatus | str = ProjectionStatus.PENDING
    embedding_status: ProjectionStatus | str = ProjectionStatus.PENDING
    projection_status: ProjectionStatus | str = ProjectionStatus.PENDING
    trace_id: str
