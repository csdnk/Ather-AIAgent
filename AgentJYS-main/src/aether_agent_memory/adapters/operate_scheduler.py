"""SchedulerPort adapter for the new representation control domain.

Legacy schedule DTOs are translated only at this boundary; they do not choose
physical placement or drive the domain lifecycle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from aether_agent_memory.b3.models import (
    ActionLogEntry,
    ExecuteStatus,
    ExecutionFeedback,
    ScheduleAction,
    ScheduleRequest,
    ScheduleRunResult,
)
from aether_agent_memory.b3.models import (
    ActionType as LegacyActionType,
)
from aether_agent_memory.core.enums import StorageTier
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.operate.controller import OperateController
from aether_agent_memory.operate.models import (
    ActionState,
    ActionType,
    ActuationTarget,
    ExecutionMode,
    PlacementObservation,
    RepresentationKind,
    RepresentationRef,
    ResourceBudget,
    TargetRole,
    TierAction,
)
from aether_agent_memory.operate.policy import ActionPlanner, PlacementPolicy, ValuePolicy
from aether_agent_memory.operate.ports import StorageControlUnavailableError
from aether_agent_memory.runtime.errors import DependencyUnavailableError, ScopeError
from aether_agent_memory.runtime.ports import MemoryStorePort
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import ComponentHealth, ComponentStatus, RuntimeComponent
from aether_agent_memory.signal.models import MemorySignal

from .storage_control import UnavailableStorageControl
from .storage_control_simulator import StorageControlSimulator


class ControlTelemetry(Protocol):
    async def read(self, memory: Memory) -> tuple[list[MemorySignal], list[AccessTrace]]: ...


class OperateSchedulerAdapter:
    def __init__(
        self,
        memory_store: MemoryStorePort,
        controller: OperateController,
        *,
        telemetry: ControlTelemetry,
        shadow_mode: bool = True,
        primary_provider: str = "sqlite",
    ) -> None:
        self.memory_store = memory_store
        self.controller = controller
        self.telemetry = telemetry
        self.shadow_mode = shadow_mode
        self.primary_provider = primary_provider
        self.value = ValuePolicy()
        self.placement = PlacementPolicy()
        self.planner = ActionPlanner()

    async def schedule(
        self, request: ScheduleRequest, context: RequestContext
    ) -> ScheduleRunResult:
        started = datetime.now(UTC)
        memories = []
        # Resolve and authorize every fact before any action can be submitted.
        for candidate in request.objects:
            memory_id = str(candidate.metadata.get("memory_id") or candidate.object_id)
            memory = await self.memory_store.get(memory_id)
            if memory is None:
                raise ValueError(f"authoritative Memory not found: {memory_id}")
            if any(
                getattr(context, field) is None or getattr(context, field) != getattr(memory, field)
                for field in ("tenant_id", "user_id", "agent_id")
            ):
                raise ScopeError("operate Memory belongs to a different agent scope")
            if context.task_id != memory.task_id:
                raise ScopeError("operate Memory belongs to a different task scope")
            memories.append(memory)
        actions: list[ScheduleAction] = []
        entries: list[ActionLogEntry] = []
        resources = ResourceBudget(network_available=request.resource_state.network_available)
        simulator = (
            self.controller.actuator
            if isinstance(self.controller.actuator, StorageControlSimulator)
            else None
        )
        if simulator:
            resources = simulator.resources()
            resources.network_available = request.resource_state.network_available
        cache = self.placement.cache_target
        tier = request.resource_state.tiers.get(StorageTier.L0_DRAM)
        if tier:
            resources.capacity_bytes[cache.key] = tier.capacity_total
            resources.used_bytes[cache.key] = tier.capacity_used
        remaining_actions = resources.max_actions
        remaining_bytes = resources.max_migration_bytes
        for memory in memories:
            scope = Scope(
                tenant_id=memory.tenant_id,
                user_id=memory.user_id,
                agent_id=memory.agent_id,
                session_id=memory.session_id,
                task_id=memory.task_id,
            )
            for pending in await self.controller.journal.pending(scope, limit=32):
                await self.controller.reconcile(pending)
            try:
                signals, traces = await self.telemetry.read(memory)
            except Exception:
                signals, traces = [], []
            for ref, size in self._representations(memory):
                if simulator and simulator._key(ref, scope) not in simulator.representations:
                    simulator.register(ref, scope, [self._primary(memory, ref)], size_bytes=size)
                try:
                    observed = await self.controller.actuator.observe(ref, scope)
                except StorageControlUnavailableError as exc:
                    raise DependencyUnavailableError(
                        str(exc), component=RuntimeComponent.B3.value, trace_id=context.trace_id
                    ) from exc
                value = self.value.evaluate(ref, memory, signals, traces)
                resources.max_actions = remaining_actions
                resources.max_migration_bytes = remaining_bytes
                plan = self.placement.plan(ref, value, observed, resources, size_bytes=size)
                proposed = self.planner.plan(
                    plan,
                    observed,
                    request_id=request.request_id,
                    size_bytes=size,
                    execution_mode=ExecutionMode.SHADOW if self.shadow_mode else ExecutionMode.LIVE,
                )
                for action in proposed:
                    if action.action_type != ActionType.KEEP:
                        if remaining_actions <= 0 or size > remaining_bytes:
                            continue
                        remaining_actions -= 1
                        remaining_bytes -= size
                        if action.action_type in {ActionType.PROMOTE, ActionType.PREFETCH}:
                            resources.used_bytes[action.target.key] = (
                                resources.used_bytes.get(action.target.key, 0) + size
                            )
                    result = await self.controller.execute(action)
                    legacy, entry = self._legacy_result(
                        result, request, value.heat, observed, plan.model_dump(mode="json")
                    )
                    actions.append(legacy)
                    entries.append(entry)
        return ScheduleRunResult(
            request_id=request.request_id,
            trace_id=context.trace_id,
            actions=actions,
            entries=entries,
            started_at=started,
            completed_at=datetime.now(UTC),
        )

    @staticmethod
    def _representations(memory: Memory) -> list[tuple[RepresentationRef, int]]:
        specs = [(RepresentationKind.CANONICAL, len(memory.content.encode("utf-8")))]
        if memory.embedding is not None and memory.vector_projection_status == "succeeded":
            specs.append((RepresentationKind.VECTOR, len(memory.embedding) * 4))
        if memory.compression_artifact_id is not None:
            specs.append(
                (
                    RepresentationKind.COMPRESSED,
                    int(
                        memory.metadata.get(
                            "compressed_utf8_bytes", len(memory.content.encode("utf-8"))
                        )
                    ),
                )
            )
        return [
            (
                RepresentationRef(
                    memory_id=memory.id,
                    representation_id=f"{memory.id}:{kind.value}",
                    representation_kind=kind,
                    revision=memory.revision,
                ),
                size,
            )
            for kind, size in specs
        ]

    def _primary(self, memory: Memory, ref: RepresentationRef) -> ActuationTarget:
        if ref.representation_kind == RepresentationKind.VECTOR:
            return ActuationTarget(
                provider="vector", namespace="memory-vectors", role=TargetRole.INDEX
            )
        if memory.placement is not None:
            return ActuationTarget(
                provider=memory.placement.provider,
                namespace=memory.placement.namespace or "memory",
                tier=memory.placement.tier.value,
                role=TargetRole.AUTHORITATIVE,
            )
        # No semantic-type -> physical-tier mapping. This source is configured by wiring.
        return ActuationTarget(
            provider=self.primary_provider, namespace="memory-facts", role=TargetRole.AUTHORITATIVE
        )

    @staticmethod
    def _legacy_result(
        action: TierAction,
        request: ScheduleRequest,
        heat: float,
        observed: PlacementObservation,
        plan: dict[str, Any],
    ) -> tuple[ScheduleAction, ActionLogEntry]:
        def tier(target: ActuationTarget) -> StorageTier:
            return (
                StorageTier(target.tier)
                if target.tier in set(StorageTier)
                else StorageTier.L3_OBJECT
            )

        metadata = {
            "operate_action": action.model_dump(mode="json"),
            "placement_plan": plan,
            "action_state": action.state.value,
            "execution_mode": action.execution_mode.value,
            "compatibility_dto": True,
            "execution_scope": "representation_target",
            "whole_memory_migration": False,
            "unknown_tier_placeholder": action.target.tier is None,
        }
        legacy = ScheduleAction(
            action_id=action.action_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            action_type=LegacyActionType(action.action_type.value),
            object_id=action.representation.memory_id,
            object_type=action.representation.representation_kind.value,
            source_tier=tier(observed.targets[0]) if observed.targets else StorageTier.L3_OBJECT,
            target_tier=tier(action.target),
            reason=action.error or "representation placement plan",
            policy_version=action.policy_version,
            expected_effect="converge representation target only",
            score=heat,
            score_frequency=0,
            score_semantic=0,
            score_decay=0,
            score_cost=0,
            tenant_id=action.scope.tenant_id,
            user_id=action.scope.user_id,
            agent_id=action.scope.agent_id,
            metadata=metadata,
        )
        # SKIPPED exists only in the unchanged legacy wire DTO, never in C's ActionState.
        status = {
            ActionState.SUCCEEDED: ExecuteStatus.SUCCESS,
            ActionState.FAILED: ExecuteStatus.FAILED,
        }.get(action.state, ExecuteStatus.SKIPPED)
        feedback = ExecutionFeedback(
            action_id=action.action_id,
            object_id=legacy.object_id,
            action_type=legacy.action_type,
            execute_status=status,
            trace_id=request.trace_id,
            failure_reason=action.error,
            # A target cache changed; there is no single new tier for the whole Memory.
            new_tier=None,
            metadata=metadata,
        )
        return legacy, ActionLogEntry(action=legacy, feedback=feedback)

    async def health(self) -> ComponentHealth:
        missing = isinstance(self.controller.actuator, UnavailableStorageControl)
        return ComponentHealth(
            component=RuntimeComponent.B3,
            status=ComponentStatus.DEGRADED if missing else ComponentStatus.HEALTHY,
            detail=(
                "Operate scheduler configured; storage control unavailable"
                if missing
                else "Representation Operate scheduler configured; provider reachability not probed"
            ),
            critical=False,
        )

    async def close(self) -> None:
        await self.controller.journal.close()
        for port in (self.controller.actuator, self.telemetry):
            close = getattr(port, "close", None)
            if close is not None:
                await close()
