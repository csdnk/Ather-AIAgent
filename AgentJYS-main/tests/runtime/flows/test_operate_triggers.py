"""Trigger-state regressions with real PG/Redis and an explicitly fake clock/Ceph.

The harness controls worker completion to make window/race boundaries deterministic;
it does not claim to execute a live Temporal worker or a live Ceph cluster.
"""

from types import SimpleNamespace

import pytest
from test_two_tier_operate import azure_redis as azure_redis
from test_two_tier_operate import dsns as dsns
from test_two_tier_operate import execution as execution
from test_two_tier_operate import intent
from test_two_tier_operate import two_tier as two_tier

from aether_agent_memory.operate.basic.continuous import ContinuousOperate, seconds
from aether_agent_memory.operate.contracts.models import SchedulingInput, Tier
from aether_agent_memory.operate.standalone.policy import (
    Settings,
    accumulate,
    evaluate,
    heat_at,
    next_delay,
)
from aether_agent_memory.recall.basic.service import Recall
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.runtime.contracts.models import EventEnvelope, Flow, RecordRef, TaskRecord
from aether_agent_memory.runtime.foundation.common import later, now
from aether_agent_memory.runtime.temporal.workflows import operate_retry_delay


class Harness:
    def __init__(self, fixture, monkeypatch):
        self.host, self.memories, _, self.item, self.cache, self.make, self.objects, self.calls = (
            fixture
        )
        self.started, self.elapsed, self.serial = now(), 0, 0
        for owner in (self.host.identity, self.host.tasks, self.host.events):
            monkeypatch.setattr(owner, "clock", self.clock)
        self.service = self.build()
        self.key = self.service.key(self.item.ref)

    def clock(self):
        return later(self.started, self.elapsed)

    def ctx(self):
        return self.host.identity.context("alice", timeout_seconds=300)

    def build(self):
        return ContinuousOperate(
            self.host.uow,
            self.host.identity,
            self.host.tasks,
            self.host.events,
            self.memories,
            self.memories,
            self.make(),
        )

    def access(self, count=1, stage="read"):
        for _ in range(count):
            self.serial += 1
            ctx = self.ctx()
            with self.host.uow.transaction() as tx:
                producer = SimpleNamespace(events=self.host.events, identity=self.host.identity)
                Recall.access(producer, tx, ctx, f"access-{self.serial}", self.item.ref, stage)
            with self.host.uow.transaction() as tx:
                events = [
                    EventEnvelope.model_validate(row["event"]) for _, row in tx.rows("outbox")
                ]
            for event in events:
                with self.host.uow.transaction() as tx:
                    self.host.events.consume(tx, "operate", event, self.service.consume)

    def view(self):
        with self.host.uow.transaction() as tx:
            return tx.read("operate_views", self.key)

    def task(self):
        with self.host.uow.transaction() as tx:
            head = tx.read("operate_evaluation_head", self.key)
            return TaskRecord.model_validate(tx.read("tasks", head["task_id"])["record"])

    def decision(self, current="cold"):
        v = self.view()
        return self.service.decide(
            self.ctx(),
            SchedulingInput(
                memory=self.item.ref,
                object_revision=1,
                storage_watermark=v["storage_watermark"],
                access_watermark=v["access_watermark"],
                successful_reads=v["successful_reads"],
                current_tier=current,
                importance=self.item.importance,
                available_bytes=1000000,
                content_bytes=len(self.item.content.encode()),
                coverage="complete",
                observed_at=self.clock(),
                policy_version="test",
            ),
        )

    async def complete(self, value):
        task = self.task()
        await self.service.before_completion(self.ctx(), task, value)
        # Simulate the worker's subsequent atomic terminal write, not another evaluate.
        with self.host.uow.transaction() as tx:
            row = tx.read("tasks", task.task_id)
            result = RecordRef(
                owner=Flow.OPERATE,
                object_type="evaluation_result",
                object_id=task.task_id,
                scope=self.item.ref.scope,
            )
            if tx.get(result) is None:
                tx.put_if_revision(result, value, None)
            row["record"]["state"] = "succeeded"
            row["record"]["result_ref"] = result.model_dump(mode="json")
            TaskRecord.model_validate(row["record"])
            tx.write("tasks", task.task_id, row)


@pytest.fixture
def h(two_tier, monkeypatch):
    return Harness(two_tier, monkeypatch)


async def test_continuous_access_records_all_inputs_without_sliding_the_window(h):
    h.access()
    assert h.service.periodic("first") == 1
    first = h.task().task_id
    decision = h.decision()
    assert decision.outcome == "keep"
    await h.complete({"decision": decision.model_dump(mode="json")})
    for elapsed in (1, 20, 40, 59):
        h.elapsed = elapsed
        h.access(3)
        assert h.service.periodic(str(elapsed)) == 0
        assert seconds(h.view()["next_evaluation_at"]) == seconds(h.started) + 60
    with h.host.uow.transaction() as tx:
        assert tx.read("operate_heat", h.key)["access_count"] == 13
        assert len(tx.rows("operate_due")) == 1
    h.elapsed = 60
    assert h.service.periodic("sixty") == 1
    assert h.task().task_id != first
    assert h.decision().target_tier == Tier.HOT


async def test_new_access_after_decision_before_commit_is_not_lost(h):
    h.access()
    assert h.service.periodic("first") == 1
    decision = h.decision()
    h.elapsed = 10
    h.access(12)
    assert h.service.periodic("busy") == 0
    await h.complete({"decision": decision.model_dump(mode="json")})
    assert not h.view()["dormant"]
    assert h.view()["wake_reason"] == "new_input"
    h.elapsed = 60
    assert h.service.periodic("followup") == 1
    assert h.decision().target_tier == Tier.HOT
    assert h.view()["access_watermark"] == 13


async def test_cold_sleeps_and_metadata_expiry_does_not_query_p2(h):
    h.access()
    assert h.service.periodic("first") == 1
    await h.complete({"decision": h.decision().model_dump(mode="json")})
    assert h.view()["dormant"]
    assert h.view()["next_evaluation_at"] is None
    calls = len(h.calls)
    for elapsed in (60, 300, 1800, 3601, 10000):
        h.elapsed = elapsed
        assert h.service.periodic(str(elapsed)) == 0
    assert len(h.calls) == calls
    with h.host.uow.transaction() as tx:
        assert tx.read("operate_heat", h.key) is None
        assert tx.rows("operate_due") == []
    h.access()
    assert h.service.periodic("wake") == 1
    assert not h.view()["dormant"]


async def test_hot_schedules_actual_crossing_instead_of_five_minute_audits(h):
    h.access(16)
    assert h.service.periodic("first") == 1
    decision = h.decision()
    assert decision.target_tier == Tier.HOT
    executor = h.service.executor
    await executor.submit(h.ctx(), intent(executor, h.ctx(), h.item, Tier.HOT, "make-hot"))
    await h.complete({"action_state": "succeeded"})
    due = seconds(h.view()["next_evaluation_at"]) - seconds(h.started)
    assert due > 300
    h.elapsed = 300
    assert h.service.periodic("no-audit") == 0
    h.elapsed = due + 1
    assert h.service.periodic("cooling") == 1
    assert h.decision(current="hot").target_tier == Tier.COLD


async def test_restart_keeps_one_due_record_and_latest_stats(h):
    h.access()
    assert h.service.periodic("first") == 1
    await h.complete({"decision": h.decision().model_dump(mode="json")})
    h.elapsed = 10
    h.access(12)
    # Reconstruct controller/provider while retaining only the runtime ports.
    # Every queue/statistic is loaded from PG, not copied from the old controller.
    old = h.service
    h.service = object.__new__(ContinuousOperate)
    for name in (
        "uow",
        "identity",
        "tasks",
        "events",
        "remember",
        "memories",
        "paused",
        "settings",
    ):
        setattr(h.service, name, getattr(old, name))
    h.service.executor = h.make()
    h.elapsed = 59
    assert h.service.periodic("before") == 0
    h.elapsed = 60
    assert h.service.periodic("after") == 1
    assert h.decision().target_tier == Tier.HOT
    assert h.view()["successful_reads"] == 13


def test_unsupported_executor_waits_for_configuration_change(h, monkeypatch):
    monkeypatch.setattr(h.service.executor, "supported_moves", ())
    h.access(13)
    for tick in range(4):
        assert h.service.periodic(str(tick)) == 0
    assert h.view()["wake_reason"] == "capability_change"
    with h.host.uow.transaction() as tx:
        assert tx.rows("operate_due") == []
        assert tx.read("operate_heat", h.key)["access_count"] == 13
    assert not h.calls
    monkeypatch.setattr(h.service.executor, "supported_moves", ("promote", "demote"))
    assert h.service.periodic("configured") == 1


async def test_failure_delays_are_persisted_and_new_access_cannot_bypass_them(h):
    h.access()
    assert h.service.periodic("first") == 1
    for index, delay in enumerate((60, 300, 900, 3600, 3600)):
        failed_at = seconds(h.clock())
        await h.complete({"deferred": "cache_capacity"})
        h.access()
        assert seconds(h.view()["next_evaluation_at"]) - failed_at == delay
        h.elapsed += delay - 1
        assert h.service.periodic(f"early-{index}") == 0
        h.elapsed += 1
        assert h.service.periodic(f"retry-{index}") == 1
    # Success resets dependency backoff.
    decision = h.decision()
    assert decision.outcome == "keep"
    await h.complete({"decision": decision.model_dump(mode="json")})
    assert h.view()["failure_count"] == 0
    assert "retry_not_before" not in h.view()


def test_cleanup_bypasses_window_backoff_and_capability_gate(h, monkeypatch):
    h.access()
    assert h.service.periodic("first") == 1
    h.decision()
    monkeypatch.setattr(h.service.executor, "supported_moves", ())
    ctx = h.ctx()
    with h.host.uow.transaction() as tx:
        view = tx.read("operate_views", h.key)
        view["cleanup"] = True
        view["permanent"] = True
        tx.write("operate_views", h.key, view)
        h.service.schedule_evaluation(
            tx,
            ctx,
            h.item.ref,
            "deletion",
            cleanup=True,
            permanent=True,
            trigger_kind="memory.changed",
        )
        rows = tx.rows("tasks")
        assert len(rows) == 2
        assert tx.rows("operate_due") == []


def test_non_read_and_duplicate_events_do_not_wake_the_scheduler(h):
    h.access()
    assert h.service.periodic("first") == 1
    h.decision()
    before = h.view()
    h.elapsed = 10
    h.access(stage="packed")
    assert h.view() == before


def test_due_pages_do_not_scan_all_cold_views_after_initial_migration(h, monkeypatch):
    h.access()
    assert h.service.periodic("first") == 1
    transaction_type = None
    with h.host.uow.transaction() as tx:
        transaction_type = type(tx)
    original = transaction_type.rows_after
    tables = []

    def measured(tx, table, cursor="", *, limit=100):
        tables.append(table)
        return original(tx, table, cursor, limit=limit)

    monkeypatch.setattr(transaction_type, "rows_after", measured)
    for i in range(5):
        assert h.service.periodic(str(i)) == 0
    assert "operate_views" not in tables
    assert tables == ["operate_due"] * 5


def test_threshold_predictor_and_no_crossing_policy(h):
    settings = Settings()
    stats = h.service.stats(h.item.ref, h.item.content_hash, 14, 0.2, None)
    start = seconds(h.clock())
    for _ in range(20):
        accumulate(stats, start, start, settings)
    evaluate(stats, start, settings)
    due = next_delay(stats, start, settings)
    assert due is not None and due > settings.audit_seconds
    assert heat_at(stats, start + due - 1, settings) >= settings.hot_down
    assert heat_at(stats, start + due, settings) < settings.hot_down
    assert next_delay(stats, start, Settings(hot_down=0.01)) is None
    assert [operate_retry_delay(n) for n in range(1, 7)] == [60, 300, 900, 3600, 3600, 3600]


async def test_production_periodic_route_uses_durable_due_page(h):
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
    from aether_agent_memory.runtime.temporal.models import PeriodicState
    from aether_agent_memory.runtime.temporal.periodic import PeriodicActivities

    h.access(13)
    ledger = ExecutionLedger(
        h.host.tasks, TemporalConfiguration(deployment_id="trigger-test", endpoint="127.0.0.1:7233")
    )
    runner = PeriodicActivities(ledger, None)
    runner.register(
        "operate_due",
        lambda tx, key, tick, _: h.service.periodic_due(tx, key, str(tick)),
        page=h.service.periodic_rows,
    )
    state = PeriodicState(deployment_id="trigger-test", last_tick=1, batch_size=2)
    result = await runner.run_periodic_batch(state)
    while result.cursor is not None:
        result = await runner.run_periodic_batch(result)
    await runner.run_periodic_batch(state)  # Replay a lost batch response.
    with h.host.uow.transaction() as tx:
        assert len(tx.rows("tasks")) == 1
        assert tx.rows("operate_due") == []
    assert h.task().deadline_at == later(h.started, 86400)
    assert h.task().max_attempts == 32


@pytest.mark.parametrize(
    "patched,kind,expected",
    [
        (True, "operate.evaluate", [60, 300, 900, 3600, 3600]),
        (False, "operate.evaluate", [1, 1, 1, 1, 1]),
        (True, "recall.execute", [1, 1, 1, 1, 1]),
    ],
)
async def test_workflow_backoff_keeps_query_mode_and_legacy_replay(
    h, monkeypatch, patched, kind, expected
):
    import asyncio
    from datetime import datetime

    from aether_agent_memory.runtime.contracts.models import EffectStatus, Flow, RecordRef
    from aether_agent_memory.runtime.temporal import workflows
    from aether_agent_memory.runtime.temporal.models import (
        StagePolicy,
        StepResult,
        TaskPlan,
        WorkflowInput,
    )

    elapsed, delays, modes = 0, [], []
    result_ref = RecordRef(
        owner=Flow.OPERATE, object_type="result", object_id="test", scope=h.item.ref.scope
    )
    query = StepResult(
        outcome="query",
        effect_status=EffectStatus.UNKNOWN,
        original_operation_id="same-action",
        reason_code="unknown",
    )
    results = [query] * 5 + [
        StepResult(
            outcome="done",
            effect_status=EffectStatus.CONFIRMED,
            result_ref=result_ref,
            reason_code="completed",
        )
    ]
    plan = TaskPlan(
        first_stage="submit",
        stages={"submit": StagePolicy(effect_mode="uncertain")},
        deadline_at=later(h.started, 86400),
        query_deadline_at=later(h.started, 86460),
        retry_seconds=1,
    )

    async def activity(name, *args, **kwargs):
        assert name == "p3.plan"
        return plan

    def start(name, step, **kwargs):
        assert name == "p3.step"
        modes.append(step.mode)
        future = asyncio.get_running_loop().create_future()
        future.set_result(results.pop(0))
        return future

    async def pause(self, delay, *, interruptible=False):
        nonlocal elapsed
        delays.append(delay)
        elapsed += delay
        assert interruptible == (patched and kind == "operate.evaluate")

    monkeypatch.setattr(workflows.workflow, "execute_activity", activity)
    monkeypatch.setattr(workflows.workflow, "start_activity", start)
    monkeypatch.setattr(workflows.workflow, "patched", lambda name: patched)
    monkeypatch.setattr(
        workflows.workflow, "now", lambda: datetime.fromisoformat(later(h.started, elapsed))
    )
    monkeypatch.setattr(workflows.P3TaskWorkflow, "pause", pause)
    result = await workflows.P3TaskWorkflow().run(
        WorkflowInput(job_id="task", kind=kind, deployment_id="test", input_hash="0" * 64)
    )
    assert result.outcome == "done"
    assert delays == expected
    assert modes == ["execute"] + ["reconcile"] * 5


async def test_completion_replay_does_not_increment_backoff(h):
    h.access()
    assert h.service.periodic("first") == 1
    await h.complete({"deferred": "cache_capacity"})
    first = h.view()
    h.elapsed = 10
    await h.complete({"deferred": "cache_capacity"})
    assert h.view()["failure_count"] == 1
    assert h.view()["next_evaluation_at"] == first["next_evaluation_at"]


async def test_cold_retirement_is_independent_of_executor_capability(h, monkeypatch):
    h.access()
    assert h.service.periodic("first") == 1
    await h.complete({"decision": h.decision().model_dump(mode="json")})
    first_due = h.view()["next_retention_at"]
    h.elapsed = 10
    await h.complete({"decision": {"outcome": "keep"}})
    assert h.view()["next_retention_at"] == first_due
    monkeypatch.setattr(h.service.executor, "supported_moves", ())
    h.elapsed = 3601
    assert h.service.periodic("retire") == 0
    with h.host.uow.transaction() as tx:
        assert tx.read("operate_heat", h.key) is None
        assert tx.rows("operate_due") == []


def test_revoked_origin_parks_due_until_authorized_input(h):
    h.access()
    ctx = h.ctx()
    with h.host.uow.transaction() as tx:
        row = tx.read("identities", ctx.principal.principal_id)
        row["enabled"] = False
        tx.write("identities", ctx.principal.principal_id, row)
    assert h.service.periodic("revoked") == 0
    assert h.view()["wake_reason"] == "authorization_required"
    with h.host.uow.transaction() as tx:
        assert tx.rows("operate_due") == []
        row = tx.read("identities", ctx.principal.principal_id)
        row["enabled"] = True
        tx.write("identities", ctx.principal.principal_id, row)
    h.access()
    assert h.service.periodic("authorized") == 1


async def test_in_progress_legacy_periodic_tick_finishes_before_due_route_upgrade(h):
    import json

    from aether_agent_memory.runtime.foundation.common import fingerprint
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
    from aether_agent_memory.runtime.temporal.models import PeriodicState
    from aether_agent_memory.runtime.temporal.periodic import PeriodicActivities

    h.access(13)
    ledger = ExecutionLedger(
        h.host.tasks, TemporalConfiguration(deployment_id="upgrade-test", endpoint="127.0.0.1:7233")
    )
    runner = PeriodicActivities(ledger, None)
    runner.register("operate_views", lambda *args: None, page=lambda *args: [])
    runner.register(
        "operate_due",
        lambda tx, key, tick, _: h.service.periodic_due(tx, key, str(tick)),
        page=h.service.periodic_rows,
    )
    state = PeriodicState(deployment_id="upgrade-test", last_tick=1, batch_size=2)
    with h.host.uow.transaction() as tx:
        tx.write(
            "temporal_ticks",
            fingerprint([state.deployment_id, state.last_tick]),
            {
                "cursor": json.dumps([0, h.key]),
                "routes": ["operate_views"],
                "interval_seconds": state.interval_seconds,
            },
        )
    result = await runner.run_periodic_batch(state)
    assert result.cursor is None
    with h.host.uow.transaction() as tx:
        assert tx.rows("tasks") == []
    result = await runner.run_periodic_batch(state.model_copy(update={"last_tick": 2}))
    while result.cursor is not None:
        result = await runner.run_periodic_batch(result)
    with h.host.uow.transaction() as tx:
        assert len(tx.rows("tasks")) == 1


def test_service_configuration_exposes_window_and_preserves_legacy_fields(tmp_path):
    from aether_agent_memory.runtime.flows.config import ServiceConfiguration
    from azure_configuration_support import settings as service_settings

    values = service_settings(tmp_path)
    values.update(operate_audit_seconds=5, operate_retry_seconds=1)
    config = ServiceConfiguration.model_validate(values)
    assert config.operate_evaluation_window_seconds == 60
    assert config.operate_evaluation_timeout_seconds == 86400
    with pytest.raises(ValueError):
        ServiceConfiguration.model_validate({**values, "operate_evaluation_window_seconds": 0})


def test_operate_budget_is_scoped_and_cleanup_keeps_the_common_limit(h):
    h.access()
    assert h.service.periodic("budget") == 1
    assert h.task().max_attempts == 32
    assert h.host.tasks.max_attempts == 3
    h.host.tasks.register("other.evaluate", "engineering", h.service)
    assert h.host.tasks.attempt_limits["other.evaluate"] == 3
    assert h.host.tasks.query_max_attempts == 6
    ctx = h.ctx()
    with h.host.uow.transaction() as tx:
        task_id = h.service.enqueue(
            tx, ctx, h.item.ref, "cleanup-budget", cleanup=True, permanent=False
        )
        assert tx.read("tasks", task_id)["record"]["max_attempts"] == 3


def test_other_task_kind_cannot_request_operate_retry_budget(h):
    from aether_agent_memory.runtime.contracts.models import ErrorCode, TaskSpec
    from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint

    h.host.tasks.register("other.evaluate", "engineering", h.service)
    ctx = h.ctx()
    ref = RecordRef(
        owner=Flow.OPERATE,
        object_type="evaluation",
        object_id="budget-test",
        scope=h.item.ref.scope,
    )
    with h.host.uow.transaction() as tx:
        tx.put_if_revision(ref, {}, None)
    with pytest.raises(FoundationError) as error, h.host.uow.transaction() as tx:
        h.host.tasks.enqueue(
            tx,
            ctx,
            TaskSpec(
                task_id="budget-test",
                owner_flow=Flow.OPERATE,
                kind="other.evaluate",
                subject=ref,
                input_ref=ref,
                idempotency_key="budget-test",
                input_hash=fingerprint({}),
                initiator_id=ctx.principal.principal_id,
                initiator_auth_epoch=ctx.principal.auth_epoch,
                deadline_at=ctx.deadline_at,
                max_attempts=32,
            ),
        )
    assert error.value.code == ErrorCode.INVALID_ARGUMENT


def test_pre_upgrade_task_replay_keeps_original_budget_and_signature(h, monkeypatch):
    ctx = h.ctx()
    with monkeypatch.context() as patch:
        patch.setattr(h.service, "evaluation_attempt_budget", lambda **kwargs: 3)
        with h.host.uow.transaction() as tx:
            task_id = h.service.enqueue(
                tx, ctx, h.item.ref, "legacy-event", cleanup=False, permanent=False
            )
            original = tx.read("tasks", task_id)
    # Call the base admission method so the coalescing fast path cannot hide a mismatch.
    from aether_agent_memory.operate.basic.service import Operate

    with h.host.uow.transaction() as tx:
        assert (
            Operate.enqueue(
                h.service, tx, ctx, h.item.ref, "legacy-event", cleanup=False, permanent=False
            )
            == task_id
        )
        assert tx.read("tasks", task_id) == original


@pytest.mark.parametrize(
    "state", ["pending", "running", "retry_wait", "recovery_wait", "attention_required"]
)
def test_old_version_evaluation_does_not_block_new_version_admission(h, state):
    h.access()
    assert h.service.periodic("old-version") == 1
    old_task = h.task()
    new_memory = h.item.ref.model_copy(update={"version": h.item.ref.version + 1})
    with h.host.uow.transaction() as tx:
        row = tx.read("tasks", old_task.task_id)
        row["record"]["state"] = state
        tx.write("tasks", old_task.task_id, row)
        # This is admission before any replica action; the original task remains intact.
        assert tx.rows("operate_actions") == []
        assert h.service.pending(tx, h.item.ref)
        assert not h.service.pending(tx, new_memory)
        different_scope = h.item.ref.model_copy(
            update={"scope": h.item.ref.scope.model_copy(update={"user_id": "other"})}
        )
        assert not h.service.pending(tx, different_scope)

    old_memory = h.item.ref
    h.item = h.item.model_copy(update={"ref": new_memory})
    new_ref = memory_ref(new_memory, versioned=True)
    with h.host.uow.transaction() as tx:
        tx.put_if_revision(new_ref, h.item.model_dump(mode="json"), None)
        tx.write("remember_current", new_memory.memory_id, new_ref.model_dump(mode="json"))
    h.access()
    assert h.service.periodic("new-version") == 1
    new_task = h.task()
    assert new_task.task_id != old_task.task_id
    with h.host.uow.transaction() as tx:
        assert tx.read("tasks", old_task.task_id) == row
        assert h.service.pending(tx, new_memory)
        assert not h.service.pending(tx, old_memory)
        assert tx.rows("operate_actions") == []
        current = tx.read("tasks", new_task.task_id)
        current["record"]["state"] = "attention_required"
        tx.write("tasks", new_task.task_id, current)
    h.access()
    assert h.service.periodic("same-version-attention") == 0
    assert h.task().task_id == new_task.task_id
    assert h.view()["wake_reason"] == "pending_completion"
