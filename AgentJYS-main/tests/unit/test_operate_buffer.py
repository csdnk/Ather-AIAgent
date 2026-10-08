"""Watermark scheduling contracts with in-process transactional records.

These tests exercise production scheduling/policy, not live PG/Redis/Temporal.
Reconstruction reuses committed records; it is not a server-restart acceptance test.
"""

from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from aether_agent_memory.operate.basic.continuous import ContinuousOperate
from aether_agent_memory.operate.basic.triggers import ACTIVE
from aether_agent_memory.operate.contracts.models import SchedulingInput
from aether_agent_memory.operate.standalone.policy import Settings, accumulate
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import ErrorCode, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.transactions import StorageTransaction
from recall_records import InMemoryRecallRecords

NOW = "2026-10-08T10:00:00.000Z"
SCOPE = Scope(tenant_id="tenant", application_id="app", user_id="alice", agent_id="agent")


class Uow:
    def __init__(self):
        self.records = InMemoryRecallRecords()

    @contextmanager
    def transaction(self):
        with self.records.transaction() as raw:
            yield StorageTransaction(raw, "test-cursor")


def build(uow=None, **settings):
    service = object.__new__(ContinuousOperate)
    service.uow = uow or Uow()
    service.settings = Settings(
        buffer_limit=10, high_watermark=3, low_watermark=1, batch_size=2, **settings
    )
    service.identity = SimpleNamespace(clock=lambda: NOW, revalidate=lambda *args: None)
    service.executor = SimpleNamespace(
        provider_id="redis", policy_managed=True, supported_moves=("promote", "demote")
    )
    service.paused = False
    return service


def seed(service, tx, name, *, scope=SCOPE, tracked=True, **updates):
    memory = MemoryRef(scope=scope, memory_id=name, version=1)
    key = service.key(memory)
    view = {
        "memory": memory.model_dump(mode="json"),
        "scheduler_event": {"test_owner": name},
        "access_watermark": 1,
        "storage_watermark": 1,
        "successful_reads": 1,
        "dormant": False,
        **updates,
    }
    service.schedule_at(tx, key, view, later(NOW, 600), "threshold_crossing")
    # Apply wake_reason/retention overrides after the ordinary schedule metadata.
    view.update(updates)
    tx.write("operate_views", key, view)
    stats = service.stats(memory, "0" * 64, 10, 0.2, None)
    heat = {**asdict(stats), "version": 1}
    if tracked:
        service.write_heat(tx, key, memory, heat)
    else:
        tx.write("operate_heat", key, heat)
    return key, memory


def drain(service):
    for _ in range(30):
        with service.uow.transaction() as tx:
            service.refresh_buffer(tx)
            if tx.read("operate_buffer_upgrade", "control")["done"] and not tx.rows(
                "operate_buffer_pressure"
            ):
                return
    pytest.fail("bounded pressure sweep did not finish")


def control(tx, scope=SCOPE):
    return tx.read("operate_buffer_scopes", fingerprint(scope.model_dump(mode="json")))


def test_crossing_high_watermark_schedules_once_and_low_watermark_rearms():
    s = build()
    with s.uow.transaction() as tx:
        a, memory = seed(s, tx, "a")
        b, _ = seed(s, tx, "b")
        for _ in range(20):
            s.write_heat(tx, a, memory, tx.read("operate_heat", a))
        assert control(tx)["count"] == 2
        assert not tx.rows("operate_buffer_pressure")
        c, _ = seed(s, tx, "c")
        assert control(tx) == {"count": 3, "latched": True, "episode": 1}
    drain(s)
    with s.uow.transaction() as tx:
        assert len(tx.rows("operate_due")) == 3
        for key in (a, b, c):
            view = tx.read("operate_views", key)
            assert view["wake_reason"] == "buffer_high_watermark"
            assert view["next_evaluation_at"] == NOW
            s.schedule_at(tx, key, view, later(NOW, 500), "threshold_crossing")
        s.refresh_buffer(tx)
        assert all(v["wake_reason"] == "threshold_crossing" for _, v in tx.rows("operate_views"))
        s.drop_heat(tx, c)
        assert control(tx)["latched"]  # Count 2 is still above the low watermark.
        s.drop_heat(tx, b)
        s.drop_heat(tx, b)  # Idempotent cleanup must not decrement twice.
        assert control(tx) == {"count": 1, "latched": False, "episode": 1}
        seed(s, tx, "b")
        seed(s, tx, "c")
        assert control(tx)["episode"] == 2
    drain(s)
    with s.uow.transaction() as tx:
        assert tx.read("operate_views", a)["wake_reason"] == "buffer_high_watermark"


@pytest.mark.parametrize(
    "dimension", ["tenant_id", "application_id", "user_id", "agent_id", "session_id", "task_id"]
)
def test_watermark_count_and_pressure_are_isolated_by_complete_scope(dimension):
    s = build()
    other = SCOPE.model_copy(update={dimension: "other"})
    with s.uow.transaction() as tx:
        untouched, _ = seed(s, tx, "other", scope=other)
        for n in range(3):
            seed(s, tx, str(n))
    drain(s)
    with s.uow.transaction() as tx:
        assert control(tx, other)["count"] == 1
        assert not control(tx, other)["latched"]
        assert tx.read("operate_views", untouched)["wake_reason"] == "threshold_crossing"


@pytest.mark.parametrize("state", sorted(ACTIVE))
def test_pressure_does_not_replace_active_retry_unknown_or_attention_tasks(state):
    s = build()
    with s.uow.transaction() as tx:
        key, memory = seed(s, tx, "busy")
        tx.write(
            "operate_evaluation_head",
            key,
            {"task_id": "task", "memory": memory.model_dump(mode="json")},
        )
        tx.write("tasks", "task", {"record": {"state": state}})
        before = tx.read("operate_views", key)
        s.pressure_wakeup(tx, key)
        assert tx.read("operate_views", key) == before
        assert tx.read("tasks", "task")["record"]["state"] == state


@pytest.mark.parametrize(
    "updates",
    [
        {"wake_reason": "retire_stats", "dormant": True},
        {"wake_reason": "stable_cold", "dormant": True},
        {"wake_reason": "authorization_required"},
        {"cleanup": True},
        {"cleanup_completed": True},
        {"scheduler_event": None},
    ],
)
def test_pressure_preserves_cleanup_retention_and_authorization_gates(updates):
    s = build()
    with s.uow.transaction() as tx:
        key, _ = seed(s, tx, "protected", **updates)
        before = tx.read("operate_views", key)
        s.pressure_wakeup(tx, key)
        assert tx.read("operate_views", key) == before


@pytest.mark.parametrize("gate", ["paused", "unsupported", "waiting_capability"])
def test_pressure_respects_capability_and_pause(gate):
    s = build()
    with s.uow.transaction() as tx:
        key, _ = seed(s, tx, "gated")
        if gate == "paused":
            s.paused = True
        elif gate == "unsupported":
            s.executor.supported_moves = ()
        else:
            view = tx.read("operate_views", key)
            view["waiting_capability"] = s.capability_signature()
            tx.write("operate_views", key, view)
        before = tx.read("operate_views", key)
        s.pressure_wakeup(tx, key)
        assert tx.read("operate_views", key) == before


def test_pressure_bypasses_input_window_but_preserves_dependency_backoff_and_earlier_due():
    s = build()
    with s.uow.transaction() as tx:
        key, _ = seed(s, tx, "retry", retry_not_before=later(NOW, 300), last_evaluated_at=NOW)
        s.pressure_wakeup(tx, key)
        assert tx.read("operate_views", key)["next_evaluation_at"] == later(NOW, 300)
        other, _ = seed(s, tx, "earlier")
        view = tx.read("operate_views", other)
        s.schedule_at(tx, other, view, NOW, "new_input")
        s.pressure_wakeup(tx, other)
        assert tx.read("operate_views", other)["wake_reason"] == "new_input"
        assert len(tx.rows("operate_due")) == 2


def test_bounded_upgrade_and_pressure_cursor_survive_service_reconstruction():
    s = build()
    with s.uow.transaction() as tx:
        for n in range(7):
            seed(s, tx, str(n), tracked=False)
        s.refresh_buffer(tx)
        assert control(tx)["count"] == 2
        assert not tx.read("operate_buffer_upgrade", "control")["done"]
        assert all(v["wake_reason"] == "threshold_crossing" for _, v in tx.rows("operate_views"))
    s = build(s.uow)
    with s.uow.transaction() as tx:
        s.refresh_buffer(tx)
        assert control(tx)["count"] == 4
        assert tx.rows("operate_buffer_pressure")
    # Simulate rollback of a page: cursor and schedule changes both disappear.
    with pytest.raises(RuntimeError), s.uow.transaction() as tx:
        s.refresh_buffer(tx)
        raise RuntimeError("rollback")
    with s.uow.transaction() as tx:
        assert control(tx)["count"] == 4
    drain(build(s.uow))
    with s.uow.transaction() as tx:
        assert control(tx)["count"] == 7 and control(tx)["episode"] == 1
        assert all(v["wake_reason"] == "buffer_high_watermark" for _, v in tx.rows("operate_views"))
        assert len(tx.rows("operate_due")) == 7


def test_retirement_releases_membership_without_deleting_memory_view():
    s = build()
    with s.uow.transaction() as tx:
        keys = [seed(s, tx, str(n))[0] for n in range(3)]
        for key in keys[:2]:
            view = tx.read("operate_views", key)
            s.schedule_at(tx, key, view, NOW, "retire_stats")
            assert s.periodic_item(tx, key, "retire") == 0
            assert tx.read("operate_heat", key) is None
            assert tx.read("operate_views", key)["scheduler_event"]
        assert control(tx)["count"] == 1 and not control(tx)["latched"]


def test_pressure_uses_each_memory_placement_authority_and_marks_revocation(monkeypatch):
    from aether_agent_memory.operate.basic import triggers

    s = build()
    contexts, admitted = [], []
    ctx = SimpleNamespace(model_copy=lambda **kwargs: ctx)

    def authority(identity, tx, key, operation_id):
        contexts.append(key)
        if len(contexts) == 2:
            raise FoundationError(ErrorCode.FORBIDDEN, "revoked")
        return ctx

    monkeypatch.setattr(triggers, "placement_context", authority)
    s.enqueue = lambda tx, context, memory, trigger, **kwargs: admitted.append((memory, kwargs))
    with s.uow.transaction() as tx:
        first, first_memory = seed(s, tx, "first")
        second, _ = seed(s, tx, "second", scope=SCOPE.model_copy(update={"tenant_id": "other"}))
        for key in (first, second):
            s.pressure_wakeup(tx, key)
            s.periodic_item(tx, key, "pressure")
        assert contexts == [first, second]
        assert len(admitted) == 1 and admitted[0][0] == first_memory
        assert admitted[0][1]["trigger_kind"] == "buffer_high_watermark"
        assert tx.read("operate_views", first)["wake_reason"] == "in_flight"
        assert tx.read("operate_views", second)["wake_reason"] == "authorization_required"


def test_high_watermark_reuses_heat_policy_not_forced_demotion():
    s = build()
    memories = []
    with s.uow.transaction() as tx:
        for name in ("low-heat", "high-heat", "third"):
            key, memory = seed(s, tx, name)
            memories.append(memory)
            if name == "high-heat":
                stats = s.stats(memory, "0" * 64, 10, 0.2, None)
                for _ in range(16):
                    accumulate(stats, stats.updated_at, stats.updated_at, s.settings)
                s.write_heat(tx, key, memory, {**asdict(stats), "version": 1})
    drain(s)
    outcomes = []
    for memory in memories[:2]:
        decision = s.decide(
            None,
            SchedulingInput(
                memory=memory,
                object_revision=1,
                storage_watermark=1,
                access_watermark=1,
                successful_reads=1,
                current_tier="hot",
                importance=0.2,
                available_bytes=100,
                content_bytes=10,
                coverage="complete",
                observed_at=NOW,
                policy_version="test",
            ),
        )
        outcomes.append((decision.outcome, decision.target_tier.value))
    assert outcomes == [("demote", "cold"), ("keep", "hot")]


def test_new_input_cannot_postpone_an_admitted_pressure_wakeup():
    s = build()
    with s.uow.transaction() as tx:
        key, memory = seed(s, tx, "window", last_evaluated_at=NOW)
        s.pressure_wakeup(tx, key)
        s.schedule_evaluation(
            tx,
            None,
            memory,
            "new-read",
            cleanup=False,
            permanent=False,
            trigger_kind="recall.access",
        )
        view = tx.read("operate_views", key)
        assert view["next_evaluation_at"] == NOW
        assert view["wake_reason"] == "buffer_high_watermark"
