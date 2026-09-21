from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from aether_agent_memory.core.scope import Scope


class RepresentationKind(StrEnum):
    CANONICAL = "canonical"
    VECTOR = "vector"
    COMPRESSED = "compressed"


class TargetRole(StrEnum):
    AUTHORITATIVE = "authoritative"
    CACHE = "cache"
    INDEX = "index"


class ActionType(StrEnum):
    KEEP = "keep"
    PREFETCH = "prefetch"
    PROMOTE = "promote"
    DEMOTE = "demote"


class ActionState(StrEnum):
    GENERATED = "generated"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ExecutionMode(StrEnum):
    SHADOW = "shadow"
    LIVE = "live"


class RepresentationRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    memory_id: str
    representation_id: str
    representation_kind: RepresentationKind
    revision: int = Field(ge=1)

    @property
    def key(self) -> str:
        # Stable across revisions, so an unresolved old action fences new work.
        return json.dumps([self.memory_id, self.representation_id, self.representation_kind])


class ActuationTarget(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    namespace: str
    tier: str | None = None
    role: TargetRole

    @property
    def key(self) -> str:
        return json.dumps([self.provider, self.namespace, self.tier, self.role])


class RepresentationValueState(BaseModel):
    representation: RepresentationRef
    heat: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    features: dict[str, float] = Field(default_factory=dict)
    policy_version: str = "heuristic-v1"
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlacementObservation(BaseModel):
    representation: RepresentationRef
    scope: Scope
    targets: list[ActuationTarget]
    epoch: int = Field(default=0, ge=0)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResourceBudget(BaseModel):
    capacity_bytes: dict[str, int] = Field(default_factory=dict)
    used_bytes: dict[str, int] = Field(default_factory=dict)
    max_actions: int = Field(default=32, ge=0)
    max_migration_bytes: int = Field(default=64 * 1024 * 1024, ge=0)
    network_available: bool = True


class RepresentationPlacementPlan(BaseModel):
    representation: RepresentationRef
    scope: Scope
    targets: list[ActuationTarget]
    observed_epoch: int
    value: RepresentationValueState
    policy_version: str = "placement-v1"
    blocked_reason: str | None = None


class TierAction(BaseModel):
    action_id: str
    representation: RepresentationRef
    scope: Scope
    action_type: ActionType
    target: ActuationTarget
    size_bytes: int = Field(ge=0)
    expected_epoch: int = Field(ge=0)
    execution_mode: ExecutionMode = ExecutionMode.SHADOW
    state: ActionState = ActionState.GENERATED
    policy_version: str
    error: str | None = None
    observed: PlacementObservation | None = None

    @property
    def fence_key(self) -> str:
        return json.dumps(
            [self.scope.as_dict(), self.representation.key, self.target.key], sort_keys=True
        )

    def intent(self) -> dict[str, object]:
        return self.model_dump(exclude={"state", "error", "observed"})


class ActionFeedback(BaseModel):
    action_id: str
    state: ActionState
    observed: PlacementObservation | None = None
    error: str | None = None
