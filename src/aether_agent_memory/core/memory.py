from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from aether_agent_memory.core.enums import MemoryState, MemoryType, SourceType, StorageTier


class P2Ref(BaseModel):
    segment_id: str
    object_key: str
    tier: StorageTier = StorageTier.L0_DRAM


class MemoryFact(BaseModel):
    kind: str
    subject: str
    predicate: str
    value: str
    normalized_key: str
    group_key: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_memory_id: str | None = None
    source_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    revision: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Memory(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    type: MemoryType
    state: MemoryState = MemoryState.ACTIVE
    session_id: str
    agent_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    task_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    source_id: str | None = None
    object_id: str | None = None
    content: str
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: SourceType = SourceType.USER
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int = 0
    importance: float = 1.0
    p2_ref: P2Ref | None = None
    superseded_by: str | None = None
    tags: list[str] = Field(default_factory=list)
    facts: list[MemoryFact] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    fact_bundle_version: str | None = None
    consolidation_status: str | None = None
    consolidated_from: list[str] = Field(default_factory=list)
    compression_artifact_id: str | None = None
    compression_status: str = "not_applicable"
    compression_ratio: float | None = None
    embedding_status: str = "pending"
    vector_projection_status: str = "pending"
    scheduler_signal_status: str = "pending"

    def touch(self) -> None:
        now = datetime.now(UTC)
        self.last_accessed_at = now
        self.access_count += 1
        self.updated_at = now

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires_at


class RecalledMemory(BaseModel):
    memory: Memory
    score: float


def normalize_projection_state(memory: Memory) -> Memory:
    """Keep top-level projection fields and metadata consistent.

    The top-level fields are authoritative; when the async pipeline wrote only
    one side, the other side is filled in so the two can never disagree.  A real
    value beats a default (``pending`` / ``not_applicable``).
    """
    meta = dict(memory.metadata)
    memory = _sync_projection(memory, meta, "embedding_status", default="pending")
    memory = _sync_projection(
        memory, meta, "compression_status", default="not_applicable"
    )
    memory = _sync_projection(memory, meta, "vector_projection_status", default="pending")
    memory = _sync_projection(memory, meta, "scheduler_signal_status", default="pending")
    return memory.model_copy(update={"metadata": meta})


def _sync_projection(
    memory: Memory, meta: dict[str, Any], field: str, *, default: str
) -> Memory:
    top = str(getattr(memory, field))
    meta_value = meta.get(field)
    if top == default and meta_value:
        memory = memory.model_copy(update={field: str(meta_value)})
    else:
        meta[field] = top
    return memory
