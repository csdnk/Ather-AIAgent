from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from aether_agent_memory.core.enums import StorageTier


class ActionType(StrEnum):
    PROMOTE = "promote"
    DEMOTE = "demote"
    KEEP = "keep"
    PIN = "pin"
    UNPIN = "unpin"
    PREFETCH = "prefetch"
    EVICT = "evict"


class ExecuteStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class AccessStats(BaseModel):
    access_frequency: float = Field(default=0.0, ge=0.0, le=1.0)
    recency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    hit_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    access_count: int = Field(default=0, ge=0)
    last_access_time: datetime | None = None


class SemanticSignals(BaseModel):
    semantic_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    task_relevance: float = Field(default=0.0, ge=0.0, le=1.0)


class TierState(BaseModel):
    tier: StorageTier
    capacity_total: int = Field(gt=0)
    capacity_used: int = Field(default=0, ge=0)
    avg_latency_ms: float = Field(default=0.0, ge=0.0)
    cost_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    allow_promote: bool = True
    allow_demote: bool = True

    @property
    def utilization(self) -> float:
        return min(self.capacity_used / self.capacity_total, 1.0)


class ResourceState(BaseModel):
    tiers: dict[StorageTier, TierState] = Field(default_factory=dict)
    migration_cost_score: float = Field(default=0.5, ge=0.0, le=1.0)
    network_available: bool = True


class SchedulableObject(BaseModel):
    object_id: str
    object_type: str
    current_tier: StorageTier
    size_bytes: int = Field(default=0, ge=0)
    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    namespace: str | None = None
    access: AccessStats = Field(default_factory=AccessStats)
    semantic: SemanticSignals = Field(default_factory=SemanticSignals)
    business_priority: float = Field(default=0.0, ge=0.0, le=1.0)
    migratable: bool = True
    pinned: bool = False
    expired: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HeuristicPolicyConfig(BaseModel):
    frequency_weight: float = 0.4
    semantic_weight: float = 0.3
    recency_weight: float = 0.2
    cost_weight: float = 0.1
    promote_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    demote_threshold: float = Field(default=0.28, ge=0.0, le=1.0)
    prefetch_threshold: float = Field(default=0.88, ge=0.0, le=1.0)
    eviction_threshold: float = Field(default=0.08, ge=0.0, le=1.0)
    target_max_utilization: float = Field(default=0.9, ge=0.0, le=1.0)
    allow_evict: bool = False
    policy_version: str = "heuristic-v1"


class ScheduleRequest(BaseModel):
    objects: list[SchedulableObject]
    resource_state: ResourceState = Field(default_factory=ResourceState)
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None


class ScheduleAction(BaseModel):
    action_id: str = Field(default_factory=lambda: uuid4().hex)
    request_id: str
    action_type: ActionType
    object_id: str
    object_type: str
    source_tier: StorageTier
    target_tier: StorageTier | None = None
    priority: int = Field(default=0, ge=0, le=100)
    reason: str
    expire_time: datetime | None = None
    trace_id: str
    policy_version: str
    expected_effect: str
    callback_required: bool = True
    score: float = Field(ge=0.0, le=1.0)
    score_frequency: float = Field(ge=0.0, le=1.0)
    score_semantic: float = Field(ge=0.0, le=1.0)
    score_decay: float = Field(ge=0.0, le=1.0)
    score_cost: float = Field(ge=0.0, le=1.0)
    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExecutionFeedback(BaseModel):
    action_id: str
    object_id: str
    action_type: ActionType
    execute_status: ExecuteStatus
    execute_latency_ms: float = Field(default=0.0, ge=0.0)
    new_tier: StorageTier | None = None
    error_code: str | None = None
    failure_reason: str | None = None
    trace_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActionLogEntry(BaseModel):
    action: ScheduleAction
    feedback: ExecutionFeedback
    fallback_action: ScheduleAction | None = None
    fallback_feedback: ExecutionFeedback | None = None


class ScheduleRunResult(BaseModel):
    request_id: str
    trace_id: str
    actions: list[ScheduleAction]
    entries: list[ActionLogEntry]
    started_at: datetime
    completed_at: datetime
