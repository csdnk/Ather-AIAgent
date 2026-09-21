"""B3 action-log persistence tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from aether_agent_memory.b3.action_log import RedisActionLogStore
from aether_agent_memory.b3.models import (
    ActionLogEntry,
    ActionType,
    ExecuteStatus,
    ExecutionFeedback,
    ScheduleAction,
    ScheduleRunResult,
)
from aether_agent_memory.core.enums import StorageTier
from aether_agent_memory.runtime.service import MemoryRuntime


class _FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.calls.append(("zadd", key, mapping))

    def expire(self, key: str, ttl: int) -> None:
        self.calls.append(("expire", key, ttl))

    def close(self) -> None:
        self.calls.append(("close",))


def _entry(
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
) -> ActionLogEntry:
    action = ScheduleAction(
        action_id="a1",
        request_id="r1",
        action_type=ActionType.KEEP,
        object_id="obj-1",
        object_type="memory",
        source_tier=StorageTier.L3_OBJECT,
        target_tier=StorageTier.L3_OBJECT,
        priority=1,
        reason="test",
        trace_id="t1",
        policy_version="heuristic-v1",
        expected_effect="none",
        score=0.5,
        score_frequency=0.5,
        score_semantic=0.5,
        score_decay=0.5,
        score_cost=0.5,
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    feedback = ExecutionFeedback(
        action_id="a1",
        object_id="obj-1",
        action_type=ActionType.KEEP,
        execute_status=ExecuteStatus.SKIPPED,
        trace_id="t1",
    )
    return ActionLogEntry(action=action, feedback=feedback)


def test_redis_action_log_persists_entries() -> None:
    fake = _FakeRedis()
    store = RedisActionLogStore("redis://localhost:6379/0", client=fake)

    store.append_entries([_entry()])
    store.close()

    assert fake.calls[0][0] == "zadd"
    assert fake.calls[0][1] == "p3:actionlog:obj-1"
    member = next(iter(fake.calls[0][2]))
    assert '"action_id":"a1"' in member
    assert fake.calls[-1][0] == "close"


def test_redis_action_log_isolates_scoped_objects_without_exposing_scope() -> None:
    fake = _FakeRedis()
    store = RedisActionLogStore("redis://localhost:6379/0", client=fake)

    store.append_entries(
        [
            _entry(tenant_id="tenant-a", user_id="user", agent_id="agent"),
            _entry(tenant_id="tenant-b", user_id="user", agent_id="agent"),
        ]
    )

    keys = [call[1] for call in fake.calls if call[0] == "zadd"]
    assert len(set(keys)) == 2
    assert all(key.startswith("p3:actionlog:v2:") for key in keys)
    assert all("tenant-" not in key for key in keys)


async def test_runtime_persist_action_log_best_effort() -> None:
    class _FakeStore:
        def __init__(self) -> None:
            self.appended: list[Any] = []

        def append_entries(self, entries: list[Any]) -> None:
            self.appended.extend(entries)

    store = _FakeStore()
    runtime = object.__new__(MemoryRuntime)
    runtime.dependencies = SimpleNamespace(action_log_store=store)
    now = datetime.now(UTC)
    result = ScheduleRunResult(
        request_id="r1",
        trace_id="t1",
        actions=[_entry().action],
        entries=[_entry()],
        started_at=now,
        completed_at=now,
    )

    await runtime._persist_action_log(result)

    assert len(store.appended) == 1
    assert store.appended[0].action.object_id == "obj-1"


async def test_runtime_persist_action_log_skips_without_store() -> None:
    runtime = object.__new__(MemoryRuntime)
    runtime.dependencies = SimpleNamespace(action_log_store=None)
    now = datetime.now(UTC)
    result = ScheduleRunResult(
        request_id="r1", trace_id="t1", actions=[], entries=[], started_at=now, completed_at=now
    )
    await runtime._persist_action_log(result)  # must not raise
