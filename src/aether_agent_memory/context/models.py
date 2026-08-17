from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory


class ContextRequest(BaseModel):
    session_id: str
    agent_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    task_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    query: str
    memory_types: list[MemoryType] = Field(
        default_factory=lambda: [MemoryType.WORKING, MemoryType.EPISODIC, MemoryType.SEMANTIC]
    )
    max_tokens: int = 4096
    max_candidates: int = 100
    deadline_ms: int = Field(default=1500, gt=0)
    filters: dict[str, Any] = Field(default_factory=dict)


class ContextPack(BaseModel):
    request: ContextRequest
    memories: list[Memory]
    total_tokens: int
    budget_tokens: int
    recall_scores: dict[str, float]
    assembled_text: str
    built_at: datetime
    summary: str = ""
    memory_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    budget_info: dict[str, int] = Field(default_factory=dict)
    status: str = "ok"
    trace_id: str | None = None
    complete: bool = True
    missing_sources: list[str] = Field(default_factory=list)
    degradation_reasons: dict[str, str] = Field(default_factory=dict)
    source_latency_ms: dict[str, float] = Field(default_factory=dict)
