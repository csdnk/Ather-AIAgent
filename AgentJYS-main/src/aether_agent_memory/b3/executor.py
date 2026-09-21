from time import perf_counter
from typing import TYPE_CHECKING, Protocol

from aether_agent_memory.b3.models import (
    ActionType,
    ExecuteStatus,
    ExecutionFeedback,
    ScheduleAction,
)
from aether_agent_memory.core.enums import StorageTier

if TYPE_CHECKING:
    from aether_agent_memory.p2.client import P2GrpcClient


class ActionExecutor(Protocol):
    async def execute(self, action: ScheduleAction) -> ExecutionFeedback: ...


class MockExecutor:
    """Deterministic P1/P2 substitute with idempotent feedback for MVP validation."""

    def __init__(
        self,
        *,
        initial_tiers: dict[str, StorageTier] | None = None,
        fail_object_ids: set[str] | None = None,
    ) -> None:
        self.current_tiers = dict(initial_tiers or {})
        self.fail_object_ids = set(fail_object_ids or set())
        self._feedback_by_action: dict[str, ExecutionFeedback] = {}

    async def execute(self, action: ScheduleAction) -> ExecutionFeedback:
        existing = self._feedback_by_action.get(action.action_id)
        if existing is not None:
            return existing

        should_fail = action.object_id in self.fail_object_ids and action.action_type not in (
            ActionType.KEEP,
            ActionType.PIN,
        )
        if should_fail:
            feedback = ExecutionFeedback(
                action_id=action.action_id,
                object_id=action.object_id,
                action_type=action.action_type,
                execute_status=ExecuteStatus.FAILED,
                execute_latency_ms=1.0,
                new_tier=self.current_tiers.get(action.object_id, action.source_tier),
                error_code="MOCK_EXECUTOR_FAILURE",
                failure_reason="configured deterministic failure",
                trace_id=action.trace_id,
            )
        else:
            resolved_tier = action.target_tier or action.source_tier
            new_tier: StorageTier | None = resolved_tier
            if action.action_type == ActionType.EVICT:
                self.current_tiers.pop(action.object_id, None)
                new_tier = None
            else:
                self.current_tiers[action.object_id] = resolved_tier
            feedback = ExecutionFeedback(
                action_id=action.action_id,
                object_id=action.object_id,
                action_type=action.action_type,
                execute_status=ExecuteStatus.SUCCESS,
                execute_latency_ms=1.0,
                new_tier=new_tier,
                trace_id=action.trace_id,
            )
        self._feedback_by_action[action.action_id] = feedback
        return feedback


class P2MigrationExecutor:
    """Execute B3 tier decisions through P2's segment control plane.

    P2 owns the write fence and route epoch. This executor deliberately does
    not pretend to move bytes: it commits a deterministic logical block route
    so the P2/P3 control-plane contract can be exercised until a physical
    tier mover is available.
    """

    name = "P2MigrationExecutor"

    def __init__(
        self,
        client: "P2GrpcClient",
        *,
        default_engine: str = "object/default",
        default_segment_id: str = "",
    ) -> None:
        self.client = client
        self.default_engine = default_engine
        self.default_segment_id = default_segment_id
        self._feedback_by_action: dict[str, ExecutionFeedback] = {}

    async def execute(self, action: ScheduleAction) -> ExecutionFeedback:
        existing = self._feedback_by_action.get(action.action_id)
        if existing is not None:
            return existing.model_copy(
                update={"metadata": {**existing.metadata, "idempotent": True}}
            )

        started = perf_counter()
        try:
            feedback = await self._execute_uncached(action, started)
        except Exception as exc:
            feedback = ExecutionFeedback(
                action_id=action.action_id,
                object_id=action.object_id,
                action_type=action.action_type,
                execute_status=ExecuteStatus.FAILED,
                execute_latency_ms=self._latency_ms(started),
                new_tier=action.source_tier,
                error_code="P2_CONTROL_FAILURE",
                failure_reason=str(exc),
                trace_id=action.trace_id,
            )
        self._feedback_by_action[action.action_id] = feedback
        return feedback

    async def _execute_uncached(
        self, action: ScheduleAction, started: float
    ) -> ExecutionFeedback:
        if action.action_type in (ActionType.KEEP, ActionType.PIN, ActionType.UNPIN):
            return ExecutionFeedback(
                action_id=action.action_id,
                object_id=action.object_id,
                action_type=action.action_type,
                execute_status=ExecuteStatus.SKIPPED,
                execute_latency_ms=self._latency_ms(started),
                new_tier=action.source_tier,
                error_code="P2_CONTROL_NOT_REQUIRED",
                failure_reason="action does not change a P2 segment route",
                trace_id=action.trace_id,
            )
        if action.action_type == ActionType.EVICT:
            return ExecutionFeedback(
                action_id=action.action_id,
                object_id=action.object_id,
                action_type=action.action_type,
                execute_status=ExecuteStatus.FAILED,
                execute_latency_ms=self._latency_ms(started),
                new_tier=action.source_tier,
                error_code="P2_EVICT_UNSUPPORTED",
                failure_reason="SegmentControl does not physically evict object data",
                trace_id=action.trace_id,
            )

        engine = str(action.metadata.get("p2_engine") or self.default_engine)
        segment_id = str(action.metadata.get("p2_segment_id") or self.default_segment_id)
        if not segment_id:
            raise ValueError("P2 segment mapping is missing from schedule action metadata")
        if action.target_tier is None:
            raise ValueError(f"{action.action_type.value} action has no target tier")

        segments = await self.client.list_segments(engine=engine)
        segment = next((item for item in segments if item.segment_id == segment_id), None)
        if segment is None:
            raise ValueError(f"P2 segment {engine}:{segment_id} was not found")

        migration_id = action.action_id
        new_block_id = f"p3-logical/{segment_id}/{migration_id}/{action.target_tier.value}"
        if segment.last_migration_id == migration_id:
            if (segment.last_outcome or "").lower() != "completed":
                raise RuntimeError(
                    f"P2 migration {migration_id} already ended as "
                    f"{segment.last_outcome or 'unknown'}"
                )
            if segment.block_ids != [new_block_id]:
                raise RuntimeError(
                    f"P2 migration {migration_id} completed with a different block route"
                )
            return self._success_feedback(
                action,
                started=started,
                engine=engine,
                segment_id=segment_id,
                migration_id=migration_id,
                route_epoch=segment.route_epoch,
                block_ids=segment.block_ids,
                idempotent=True,
            )

        freeze = await self.client.freeze_segment(
            segment_id,
            engine=engine,
            migration_id=migration_id,
            expected_route_epoch=segment.route_epoch,
        )
        try:
            complete = await self.client.complete_migration(
                segment_id,
                engine=engine,
                migration_id=migration_id,
                new_block_ids=[new_block_id],
                expected_route_epoch=freeze.route_epoch,
            )
        except Exception as exc:
            try:
                await self.client.fail_migration(
                    segment_id,
                    engine=engine,
                    migration_id=migration_id,
                    expected_route_epoch=freeze.route_epoch,
                    reason=str(exc),
                )
            except Exception as rollback_exc:
                raise RuntimeError(
                    f"P2 migration failed ({exc}); rollback also failed ({rollback_exc})"
                ) from exc
            raise

        return self._success_feedback(
            action,
            started=started,
            engine=engine,
            segment_id=segment_id,
            migration_id=migration_id,
            route_epoch=complete.route_epoch,
            block_ids=complete.block_ids,
            idempotent=freeze.idempotent or complete.idempotent,
        )

    def _success_feedback(
        self,
        action: ScheduleAction,
        *,
        started: float,
        engine: str,
        segment_id: str,
        migration_id: str,
        route_epoch: int,
        block_ids: list[str],
        idempotent: bool,
    ) -> ExecutionFeedback:
        return ExecutionFeedback(
            action_id=action.action_id,
            object_id=action.object_id,
            action_type=action.action_type,
            execute_status=ExecuteStatus.SUCCESS,
            execute_latency_ms=self._latency_ms(started),
            new_tier=action.target_tier,
            trace_id=action.trace_id,
            metadata={
                "p2_engine": engine,
                "p2_segment_id": segment_id,
                "migration_id": migration_id,
                "route_epoch": route_epoch,
                "block_ids": block_ids,
                "idempotent": idempotent,
                "route_mode": "logical",
            },
        )

    @staticmethod
    def _latency_ms(started: float) -> float:
        return max((perf_counter() - started) * 1000, 0.0)
