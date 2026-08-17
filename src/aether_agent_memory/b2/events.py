from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from aether_agent_memory.core.enums import SourceType


class MemoryEventType(StrEnum):
    AFTER_TURN = "after_turn"
    TASK_UPDATE = "task_update"
    RAG_RESULT = "rag_result"
    TOOL_RESULT = "tool_result"
    USER_MEMORY = "user_memory"


class MemoryEvent(BaseModel):
    event_type: MemoryEventType
    session_id: str
    agent_id: str
    content: str
    user_id: str | None = None
    tenant_id: str | None = None
    task_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    source_id: str | None = None
    object_id: str | None = None
    source: SourceType = SourceType.USER
    importance: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
