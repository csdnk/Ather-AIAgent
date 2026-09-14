from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.operate.models import (
    ActionType,
    ActuationTarget,
    ExecutionMode,
    PlacementObservation,
    RepresentationKind,
    RepresentationPlacementPlan,
    RepresentationRef,
    RepresentationValueState,
    ResourceBudget,
    TargetRole,
    TierAction,
)
from aether_agent_memory.signal.models import MemorySignal


class ValuePolicy:
    """Importance is an input; heat is a scoped, representation-specific estimate."""

    def evaluate(
        self,
        ref: RepresentationRef,
        memory: Memory,
        signals: list[MemorySignal],
        traces: list[AccessTrace],
    ) -> RepresentationValueState:
        if ref.memory_id != memory.id or ref.revision != memory.revision:
            raise ValueError("value input must match the authoritative Memory revision")
        fields = ("tenant_id", "user_id", "agent_id", "session_id")

        def matches(event: MemorySignal | AccessTrace) -> bool:
            return (
                event.memory_id == memory.id
                and all(getattr(event, field) == getattr(memory, field) for field in fields)
                and (
                    event.task_id
                    if isinstance(event, AccessTrace)
                    else event.metadata.get("task_id")
                )
                == memory.task_id
                and event.metadata.get("representation_id", ref.representation_id)
                == ref.representation_id
                and event.metadata.get("representation_kind", "canonical")
                == ref.representation_kind
            )

        hits = [trace for trace in traces if trace.hit and matches(trace)]
        selected = [signal for signal in signals if matches(signal)]
        importance = min(max(memory.importance, 0.0), 1.0)
        now = datetime.now(UTC)

        def freshness(timestamp: datetime) -> float:
            return math.exp(-max((now - timestamp).total_seconds(), 0) / 3600)

        evidence_weight = sum(freshness(t.timestamp) for t in hits) + sum(
            freshness(s.timestamp) for s in selected
        )
        frequency = 1 - math.exp(-evidence_weight / 5)
        timestamps = [event.timestamp for event in hits] + [event.timestamp for event in selected]
        age = max((datetime.now(UTC) - max(timestamps)).total_seconds(), 0) if timestamps else None
        recency = math.exp(-age / 3600) if age is not None else 0.0
        relevance = sum(
            min(max(hit.score or 0, 0), 1) * freshness(hit.timestamp) for hit in hits
        ) / max(len(hits), 1)
        semantic = (importance + relevance) / 2
        heat = min(1.0, 0.4 * frequency + 0.4 * semantic + 0.2 * recency)
        return RepresentationValueState(
            representation=ref,
            heat=heat,
            importance=importance,
            features={"frequency": frequency, "semantic": semantic, "recency": recency},
        )


class PlacementPolicy:
    def __init__(self, *, cache_target: ActuationTarget | None = None) -> None:
        self.cache_target = cache_target or ActuationTarget(
            provider="redis",
            namespace="memory-cache",
            tier="L0",
            role=TargetRole.CACHE,
        )

    def plan(
        self,
        ref: RepresentationRef,
        value: RepresentationValueState,
        observed: PlacementObservation,
        resources: ResourceBudget,
        *,
        size_bytes: int,
    ) -> RepresentationPlacementPlan:
        if value.representation != ref or observed.representation != ref:
            raise ValueError("placement requires the current representation revision")
        targets = list(observed.targets)
        blocked = None
        age = (datetime.now(UTC) - observed.observed_at).total_seconds()
        if age > 60 or age < -5:
            blocked = "stale_observation"
        elif not resources.network_available:
            blocked = "network_unavailable"
        elif ref.representation_kind != RepresentationKind.VECTOR:
            cache = self.cache_target
            authoritative = [t for t in targets if t.role == TargetRole.AUTHORITATIVE]
            if not authoritative:
                blocked = "authoritative_placement_unknown"
            elif value.heat >= 0.7 and cache not in targets:
                free = resources.capacity_bytes.get(cache.key, 0) - resources.used_bytes.get(
                    cache.key, 0
                )
                if (
                    size_bytes <= free
                    and resources.max_actions > 0
                    and size_bytes <= resources.max_migration_bytes
                ):
                    targets.append(cache)
                else:
                    blocked = "capacity_or_budget"
            elif value.heat < 0.25:
                targets = [t for t in targets if t.role != TargetRole.CACHE]
        return RepresentationPlacementPlan(
            representation=ref,
            scope=observed.scope,
            targets=targets,
            observed_epoch=observed.epoch,
            value=value,
            blocked_reason=blocked,
        )


class ActionPlanner:
    def plan(
        self,
        plan: RepresentationPlacementPlan,
        observed: PlacementObservation,
        *,
        request_id: str,
        size_bytes: int,
        execution_mode: ExecutionMode = ExecutionMode.SHADOW,
    ) -> list[TierAction]:
        if plan.representation != observed.representation or plan.scope != observed.scope:
            raise ValueError("action plan scope/revision mismatch")
        changes = [(ActionType.PROMOTE, t) for t in plan.targets if t not in observed.targets]
        changes += [(ActionType.DEMOTE, t) for t in observed.targets if t not in plan.targets]
        if any(
            t.role == TargetRole.AUTHORITATIVE for kind, t in changes if kind == ActionType.DEMOTE
        ):
            raise ValueError("cannot reclaim authoritative representation")
        if not changes:
            changes = [(ActionType.KEEP, t) for t in plan.targets[:1]]
        return [
            TierAction(
                action_id=uuid5(
                    NAMESPACE_URL,
                    f"{request_id}:{plan.scope}:{plan.representation}:{kind}:{target.key}",
                ).hex,
                representation=plan.representation,
                scope=plan.scope,
                action_type=kind,
                target=target,
                size_bytes=size_bytes,
                expected_epoch=plan.observed_epoch,
                execution_mode=execution_mode,
                policy_version=plan.policy_version,
            )
            for kind, target in changes
        ]
