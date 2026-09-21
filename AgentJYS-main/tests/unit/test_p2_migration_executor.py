from __future__ import annotations

import pytest

from aether_agent_memory.b3 import (
    ActionType,
    P2MigrationExecutor,
    SchedulableObject,
    ScheduleRequest,
    SemanticSignals,
)
from aether_agent_memory.b3.heuristic import HeuristicPolicy
from aether_agent_memory.b3.models import AccessStats, ExecuteStatus
from aether_agent_memory.core.enums import StorageTier
from aether_agent_memory.p2.client import P2MigrationAck, P2SegmentInfo


class _FakeP2:
    def __init__(self, *, fail_complete: bool = False) -> None:
        self.fail_complete = fail_complete
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.route_epoch = 0
        self.block_ids = ["old"]
        self.last_migration_id: str | None = None
        self.last_outcome: str | None = None

    async def list_segments(self, *, engine: str) -> list[P2SegmentInfo]:
        self.calls.append(("list", {"engine": engine}))
        return [
            P2SegmentInfo(
                engine=engine,
                segment_id="p3-memory",
                state="sealed",
                route_epoch=self.route_epoch,
                block_ids=list(self.block_ids),
                last_migration_id=self.last_migration_id,
                last_outcome=self.last_outcome,
            )
        ]

    async def freeze_segment(self, segment_id: str, **kwargs: object) -> P2MigrationAck:
        self.calls.append(("freeze", {"segment_id": segment_id, **kwargs}))
        return P2MigrationAck(
            ok=True,
            engine=str(kwargs["engine"]),
            segment_id=segment_id,
            migration_id=str(kwargs["migration_id"]),
            route_epoch=self.route_epoch,
            idempotent=False,
            state="frozen",
            block_ids=["old"],
        )

    async def complete_migration(self, segment_id: str, **kwargs: object) -> P2MigrationAck:
        self.calls.append(("complete", {"segment_id": segment_id, **kwargs}))
        if self.fail_complete:
            raise RuntimeError("destination unavailable")
        self.route_epoch += 1
        self.block_ids = [str(value) for value in kwargs["new_block_ids"]]  # type: ignore[union-attr]
        self.last_migration_id = str(kwargs["migration_id"])
        self.last_outcome = "completed"
        return P2MigrationAck(
            ok=True,
            engine=str(kwargs["engine"]),
            segment_id=segment_id,
            migration_id=str(kwargs["migration_id"]),
            route_epoch=self.route_epoch,
            idempotent=False,
            state="sealed",
            block_ids=list(self.block_ids),
            outcome="completed",
        )

    async def fail_migration(self, segment_id: str, **kwargs: object) -> P2MigrationAck:
        self.calls.append(("failed", {"segment_id": segment_id, **kwargs}))
        self.last_migration_id = str(kwargs["migration_id"])
        self.last_outcome = "failed"
        return P2MigrationAck(
            ok=True,
            engine=str(kwargs["engine"]),
            segment_id=segment_id,
            migration_id=str(kwargs["migration_id"]),
            route_epoch=self.route_epoch,
            idempotent=False,
            state="sealed",
            block_ids=["old"],
            outcome="failed",
        )


def _action():
    obj = SchedulableObject(
        object_id="document-1",
        object_type="document",
        current_tier=StorageTier.L3_OBJECT,
        access=AccessStats(access_frequency=1.0, recency_score=1.0, hit_rate=1.0),
        semantic=SemanticSignals(
            semantic_relevance=1.0,
            importance=1.0,
            task_relevance=1.0,
        ),
        business_priority=1.0,
        metadata={"p2_engine": "object/default", "p2_segment_id": "p3-memory"},
    )
    request = ScheduleRequest(objects=[obj])
    return HeuristicPolicy().decide(request)[0]


@pytest.mark.unit
async def test_p2_executor_freezes_and_completes_logical_route() -> None:
    p2 = _FakeP2()
    executor = P2MigrationExecutor(p2)
    action = _action()

    feedback = await executor.execute(action)

    assert feedback.execute_status == ExecuteStatus.SUCCESS
    assert feedback.new_tier == StorageTier.L2_HDD
    assert feedback.metadata["migration_id"] == action.action_id
    assert feedback.metadata["route_epoch"] == 1
    assert feedback.metadata["route_mode"] == "logical"
    assert [name for name, _ in p2.calls] == ["list", "freeze", "complete"]
    assert p2.calls[1][1]["expected_route_epoch"] == 0
    assert p2.calls[2][1]["expected_route_epoch"] == 0


@pytest.mark.unit
async def test_p2_executor_reports_failure_and_releases_fence() -> None:
    p2 = _FakeP2(fail_complete=True)
    executor = P2MigrationExecutor(p2)

    feedback = await executor.execute(_action())

    assert feedback.execute_status == ExecuteStatus.FAILED
    assert feedback.error_code == "P2_CONTROL_FAILURE"
    assert [name for name, _ in p2.calls] == ["list", "freeze", "complete", "failed"]


@pytest.mark.unit
async def test_p2_executor_caches_action_feedback_and_skips_local_actions() -> None:
    p2 = _FakeP2()
    executor = P2MigrationExecutor(p2)
    action = _action()

    first = await executor.execute(action)
    second = await executor.execute(action)
    keep = action.model_copy(
        update={"action_id": "keep-action", "action_type": ActionType.KEEP}
    )
    keep_feedback = await executor.execute(keep)

    assert first.execute_status == second.execute_status == ExecuteStatus.SUCCESS
    assert first.metadata["idempotent"] is False
    assert second.metadata["idempotent"] is True
    assert second.metadata["route_epoch"] == first.metadata["route_epoch"]
    assert len([name for name, _ in p2.calls if name == "list"]) == 1
    assert keep_feedback.execute_status == ExecuteStatus.SKIPPED


@pytest.mark.unit
async def test_p2_executor_replays_completed_action_after_executor_restart() -> None:
    p2 = _FakeP2()
    action = _action()

    first = await P2MigrationExecutor(p2).execute(action)
    second = await P2MigrationExecutor(p2).execute(action)

    assert first.execute_status == second.execute_status == ExecuteStatus.SUCCESS
    assert second.metadata["idempotent"] is True
    assert second.metadata["route_epoch"] == first.metadata["route_epoch"]
    assert [name for name, _ in p2.calls] == ["list", "freeze", "complete", "list"]
