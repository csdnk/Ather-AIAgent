from types import SimpleNamespace

import pytest
from test_p3_admin_diagnostics import operator
from test_placement_monitor import action

from aether_agent_memory.operate.basic.continuous import ContinuousOperate
from aether_agent_memory.operate.basic.service import Operate
from aether_agent_memory.runtime.foundation.common import now
from aether_agent_memory.runtime.storage.redis_executor import RedisExecutor


@pytest.mark.parametrize("cleanup,expected", [(False, 0), (True, 1)])
def test_memory_events_keep_observations_but_only_admit_necessary_cleanup(
    tmp_path, cleanup, expected
):
    from aether_agent_memory.operate.standalone.policy import Settings
    from aether_agent_memory.remember.basic.service import memory_ref
    from aether_agent_memory.remember.contracts.models import MemoryRef, StorageChanged
    from aether_agent_memory.runtime.contracts.models import EventEnvelope
    from aether_agent_memory.runtime.foundation.common import fingerprint

    identity, ctx, _ = operator(tmp_path)
    service = object.__new__(ContinuousOperate)
    service.uow, service.identity = identity.uow, identity
    service.executor = SimpleNamespace(supported_moves=())
    service.settings = Settings()
    service.memories = SimpleNamespace(
        final_guard=lambda *args: SimpleNamespace(items=[SimpleNamespace(decision="allowed")])
    )
    calls = []
    service.enqueue = lambda *args, **kwargs: calls.append(kwargs)
    memory = MemoryRef.model_validate(action()["intent"]["decision"]["memory"])
    payload = StorageChanged(
        memory=memory,
        object_revision=1,
        change="deleted" if cleanup else "saved",
        status="deleted" if cleanup else "active",
        projection_state="not_required",
        content_hash="a" * 64,
        source_count=1,
        content_bytes=20,
    ).model_dump(mode="json")
    event = EventEnvelope(
        event_id="event1",
        event_type="memory.changed",
        producer="remember",
        subject=memory_ref(memory),
        subject_revision=1,
        occurred_at=now(),
        request_id=ctx.request_id,
        trace_id=ctx.trace_id,
        initiator_id=ctx.principal.principal_id,
        initiator_auth_epoch=ctx.principal.auth_epoch,
        payload=payload,
        payload_hash=fingerprint(payload),
    )
    with identity.uow.transaction() as tx:
        service.consume(tx, event)
        view = tx.read("operate_views", service.key(memory))
        assert view["event"]["event_id"] == "event1"
        assert view["content_bytes"] == 20
    assert len(calls) == expected
    if cleanup:
        assert calls[0]["cleanup"] and calls[0]["permanent"]


def test_heat_cleanup_removes_only_target_and_releases_buffer_pressure(tmp_path):
    from aether_agent_memory.operate.standalone.policy import Settings
    from aether_agent_memory.remember.contracts.models import MemoryRef
    from aether_agent_memory.runtime.foundation.common import fingerprint

    identity, _, _ = operator(tmp_path)
    service = object.__new__(ContinuousOperate)
    service.settings = Settings(high_watermark=2, low_watermark=1)
    memory = MemoryRef.model_validate(action()["intent"]["decision"]["memory"])
    other = memory.model_copy(update={"memory_id": "other-memory"})
    key, other_key = service.key(memory), service.key(other)
    scope_key = fingerprint(memory.scope.model_dump(mode="json"))
    with identity.uow.transaction() as tx:
        service.write_heat(tx, key, memory, {"version": 1})
        service.write_heat(tx, other_key, other, {"version": 1})
        tx.write("operate_views", key, {"retained": True})
        assert tx.read("operate_buffer_pressure", scope_key) is not None

        service.drop_heat(tx, key)
        service.drop_heat(tx, key)

        assert tx.read("operate_heat", key) is None
        assert tx.read("operate_buffer_members", key) is None
        assert tx.read("operate_buffer_index", scope_key + ":" + key) is None
        assert tx.read("operate_heat", other_key) == {"version": 1}
        assert tx.read("operate_buffer_members", other_key) == {"scope_key": scope_key}
        assert tx.read("operate_buffer_index", scope_key + ":" + other_key) == {
            "memory_key": other_key
        }
        assert tx.read("operate_views", key) == {"retained": True}
        assert tx.read("operate_buffer_scopes", scope_key) == {
            "count": 1,
            "latched": False,
            "episode": 1,
        }
        assert tx.read("operate_buffer_pressure", scope_key) is None


def test_hot_cache_declares_that_it_cannot_move_between_tiers():
    assert getattr(RedisExecutor, "supported_moves", None) == ()


def test_due_checks_skip_unsupported_moves_before_auth_or_full_task_scan(tmp_path):
    identity, _, _ = operator(tmp_path)
    service = object.__new__(ContinuousOperate)
    service.executor = SimpleNamespace(supported_moves=())
    service.identity = SimpleNamespace(clock=now)
    service.settings = SimpleNamespace(retry_seconds=5)
    memory = action()["intent"]["decision"]["memory"]
    with identity.uow.transaction() as tx:
        tx.write(
            "operate_views",
            "one",
            {
                "memory": memory,
                "scheduler_event": {"old": "invalidated event"},
                "next_evaluation_at": "2026-01-01T00:00:00.000Z",
                "cleanup": False,
            },
        )
        assert service.periodic_item(tx, "one", "tick1") == 0
        assert tx.rows("tasks") == []


def test_capability_gate_keeps_cleanup_admission_and_unknown_providers_compatible():
    service = object.__new__(Operate)
    service.executor = SimpleNamespace(supported_moves=())
    assert hasattr(service, "should_evaluate")
    assert not service.should_evaluate(cleanup=False)
    assert service.should_evaluate(cleanup=True)
    service.executor = SimpleNamespace(supported_moves=("promote", "demote"))
    assert service.should_evaluate(cleanup=False)
    service.executor = SimpleNamespace()
    assert service.should_evaluate(cleanup=False)
