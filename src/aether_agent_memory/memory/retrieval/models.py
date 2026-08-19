from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from aether_agent_memory.core.enums import MemoryType, StorageTier


class RecallSourceName(StrEnum):
    WORKING = "working"
    LONG_TERM = "long_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class RecallCandidate(BaseModel):
    memory_id: str
    content: str | None = None
    content_ref: str | None = None
    source: str
    score: float = 0.0
    semantic_score: float | None = None
    temporal_score: float | None = None
    memory_type: MemoryType | None = None
    created_at: datetime | None = None
    last_access: datetime | None = None
    trace_metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRetrievalResult(BaseModel):
    candidates: list[RecallCandidate] = Field(default_factory=list)
    complete: bool = True
    missing_sources: list[str] = Field(default_factory=list)
    degraded_reasons: dict[str, str] = Field(default_factory=dict)
    source_latency_ms: dict[str, float] = Field(default_factory=dict)
    trace_id: str | None = None


class AccessTrace(BaseModel):
    trace_id: str
    request_id: str
    memory_id: str
    source: str
    tier: StorageTier | str | None = None
    hit: bool = True
    score: float | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
