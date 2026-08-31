from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.memory.models import ProjectionStatus

if TYPE_CHECKING:
    from aether_agent_memory.b2.events import MemoryEvent


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


class FormationAction(StrEnum):
    CREATE_MEMORY = "CREATE_MEMORY"
    NO_MEMORY = "NO_MEMORY"
    PENDING = "PENDING"


class FormationDecision(BaseModel):
    action: FormationAction
    memory_type: MemoryType | None = None
    source: SourceType = SourceType.USER
    reason: str = "default-compatible-policy"


class MemoryFormationPolicy(Protocol):
    def decide(self, event: MemoryEvent) -> FormationDecision: ...


class DefaultMemoryFormationPolicy:
    """Compatibility policy for event-to-memory formation.

    The current product rule is intentionally simple: explicit user memories
    become semantic memories, while other observations remain working memories.
    """

    def decide(self, event: MemoryEvent) -> FormationDecision:
        event_type = event.event_type.value
        source = event.source
        if event_type == "rag_result":
            source = SourceType.RAG
        elif event_type == "tool_result":
            source = SourceType.TOOL
        memory_type = MemoryType.SEMANTIC if event_type == "user_memory" else MemoryType.WORKING
        return FormationDecision(
            action=FormationAction.CREATE_MEMORY,
            memory_type=memory_type,
            source=source,
        )
