from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from aether_agent_memory.context_store.models import (
    ContextCandidate,
    ContextItemKind,
    ContextLayer,
    RetrievalTrace,
)
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.enums import MemoryType, StorageTier
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope


class RecallSourceName(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    LONG_DOCUMENT = "long_document"


class RecallCandidate(BaseModel):
    """Compatibility DTO accepted from existing memory-oriented recall sources."""

    memory_id: str
    context_uri: AetherUri | None = None
    context_kind: ContextItemKind | None = None
    context_layer: ContextLayer = ContextLayer.DETAIL
    scope: Scope | None = None
    memory: Memory | None = Field(default=None, exclude=True)
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


class RecallSourceResult(BaseModel):
    """Typed source outcome that can retain useful hits during degradation."""

    candidates: list[RecallCandidate] = Field(default_factory=list)
    complete: bool = True
    missing_sources: list[str] = Field(default_factory=list)
    degraded_reasons: dict[str, str] = Field(default_factory=dict)


class MemoryRetrievalResult(BaseModel):
    candidates: list[RecallCandidate] = Field(default_factory=list)
    context_candidates: list[ContextCandidate] = Field(default_factory=list, exclude=True)
    complete: bool = True
    missing_sources: list[str] = Field(default_factory=list)
    degraded_reasons: dict[str, str] = Field(default_factory=dict)
    source_latency_ms: dict[str, float] = Field(default_factory=dict)
    trace_id: str | None = None
    retrieval_trace: RetrievalTrace | None = Field(default=None, exclude=True)


class AccessTrace(BaseModel):
    trace_id: str
    request_id: str
    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    memory_id: str
    source: str
    tier: StorageTier | str | None = None
    hit: bool = True
    score: float | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
