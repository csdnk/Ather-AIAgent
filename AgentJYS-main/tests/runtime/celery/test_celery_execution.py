"""The business ledger, not Celery result state, owns durable progress."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "temporal"))

from test_ledger import admit
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger

from aether_agent_memory.runtime.celery.config import CeleryConfiguration
from aether_agent_memory.runtime.celery.execution import CeleryExecution
from aether_agent_memory.runtime.contracts.models import EffectStatus, Permission
from aether_agent_memory.runtime.temporal.activities import StageContext
from aether_agent_memory.runtime.temporal.models import StepResult
from aether_agent_memory.runtime.temporal.registry import StageRegistry


def executor(ledger, execute, reconcile=None):
    registry = StageRegistry()
    registry.register(
        "engineering.save",
        "write",
        execute,
        reconcile or execute,
        Permission.WRITE,
        "uncertain",
        timeout_seconds=1,
    )
    result = CeleryExecution(ledger, registry, CeleryConfiguration(broker_url_env="TEST_BROKER"))
    ledger.backend_admission = result.bind
    return result


def test_admission_is_atomic_and_has_no_temporal_start(foundation, ledger):
    async def unused(step):
        pytest.fail("admission must not execute a handler")

    engine = executor(ledger, unused)
    job, _ = admit(foundation, ledger)
    with foundation.uow.transaction() as tx:
        assert tx.read("temporal_bindings", job.job_id)["backend"] == "celery"
        assert tx.read("celery_jobs", job.job_id)["state"] == "pending"
        assert len(tx.celery_due_rows(ledger.tasks.clock())) == 1
        assert not tx.pending_intent_rows("start")
    engine.close()


@pytest.mark.asyncio
async def test_future_generation_and_revoked_identity_never_call_provider(foundation, ledger):
    calls = []

    async def execute(step):
        calls.append(step)
        return StepResult(
            outcome="attention", effect_status=EffectStatus.UNKNOWN, reason_code="UNRESOLVED"
        )

    engine = executor(ledger, execute)
    job, _ = admit(foundation, ledger)
    await engine.run(job.job_id, 42)
    with foundation.uow.transaction() as tx:
        identity = tx.read("identities", "alice")
        tx.write("identities", "alice", {**identity, "enabled": False})
    await engine.run(job.job_id, 0)
    assert calls == []
    with foundation.uow.transaction() as tx:
        assert ledger.tasks.load(tx, job.job_id)[1].state == "failed"
    engine.close()


@pytest.mark.asyncio
async def test_duplicate_delivery_does_not_repeat_committed_effect(foundation, ledger):
    calls = []

    async def execute(step):
        calls.append("write")
        context = StageContext.current()
        ref = context.task.subject.model_copy(update={"object_type": "result"})
        with foundation.uow.transaction() as tx:
            tx.put_if_revision(ref, {"answer": 42}, None)
        return StepResult(
            outcome="done",
            result_ref=ref,
            effect_status=EffectStatus.CONFIRMED,
            reason_code="SAVED",
        )

    engine = executor(ledger, execute)
    job, _ = admit(foundation, ledger)
    await engine.run(job.job_id, 0)
    await engine.run(job.job_id, 0)
    assert calls == ["write"]
    with foundation.uow.transaction() as tx:
        assert ledger.tasks.load(tx, job.job_id)[1].state == "succeeded"
    engine.close()


@pytest.mark.asyncio
async def test_unknown_effect_is_queried_before_any_retry(foundation, ledger):
    calls = []

    async def execute(step):
        calls.append("write")
        return StepResult(
            outcome="query", effect_status=EffectStatus.UNKNOWN, reason_code="ACK_LOST"
        )

    async def reconcile(step):
        calls.append("query")
        return StepResult(
            outcome="attention", effect_status=EffectStatus.UNKNOWN, reason_code="UNRESOLVED"
        )

    engine = executor(ledger, execute, reconcile)
    job, _ = admit(foundation, ledger)
    await engine.run(job.job_id, 0)
    with foundation.uow.transaction() as tx:
        row = tx.read("celery_jobs", job.job_id)
        tx.write("celery_jobs", job.job_id, {**row, "due_at": ledger.tasks.clock()})
    await engine.run(job.job_id, 1)
    assert calls == ["write", "query"]
    with foundation.uow.transaction() as tx:
        task = ledger.tasks.load(tx, job.job_id)[1]
        assert task.state == "attention_required" and task.effect_status == "unknown"
    engine.close()


@pytest.mark.asyncio
async def test_changed_input_settles_without_provider(foundation, ledger):
    async def execute(step):
        pytest.fail("changed input must not reach provider")

    engine = executor(ledger, execute)
    job, _ = admit(foundation, ledger)
    with foundation.uow.transaction() as tx:
        task = ledger.tasks.load(tx, job.job_id)[1]
        tx.put_if_revision(task.input_ref, {"changed": True}, tx.revision(task.input_ref))
    await engine.run(job.job_id, 0)
    with foundation.uow.transaction() as tx:
        assert ledger.tasks.load(tx, job.job_id)[1].state == "failed"
    engine.close()


@pytest.mark.asyncio
async def test_live_owner_and_expired_fence(foundation, ledger):
    from aether_agent_memory.runtime.foundation.common import FoundationError

    async def unused(step):
        pytest.fail("claim alone cannot invoke handler")

    engine = executor(ledger, unused)
    job, _ = admit(foundation, ledger)
    step, deadline, execution = engine.claim(job.job_id, 0, None, None)
    context = engine.activities.begin_step(step, deadline, execution)
    assert engine.claim(job.job_id, 0, None, None) is None
    with foundation.uow.transaction() as tx:
        row = tx.read("celery_jobs", job.job_id)
        tx.write("celery_jobs", job.job_id, {**row, "lease_until": "2000-01-01T00:00:00Z"})
    with pytest.raises(FoundationError), foundation.uow.transaction() as tx:
        context.commit_fence(tx)
    engine.close()


@pytest.mark.asyncio
async def test_revoked_unknown_owner_stays_unknown_and_late_finish_is_ignored(foundation, ledger):
    async def unused(step):
        pytest.fail("revoked owner cannot run a provider")

    engine = executor(ledger, unused)
    job, _ = admit(foundation, ledger)
    step, deadline, execution = engine.claim(job.job_id, 0, None, None)
    engine.activities.begin_step(step, deadline, execution)
    with foundation.uow.transaction() as tx:
        row = tx.read("celery_jobs", job.job_id)
        tx.write(
            "celery_jobs",
            job.job_id,
            {**row, "generation": 1, "lease_until": "2000-01-01T00:00:00Z"},
        )
        identity = tx.read("identities", "alice")
        tx.write("identities", "alice", {**identity, "enabled": False})
    stale = engine.finish(
        step,
        None,
        StepResult(outcome="failed", effect_status=EffectStatus.NO_EFFECT, reason_code="STALE"),
        generation=0,
        execution=execution,
    )
    assert stale is None
    await engine.run(job.job_id, 1)
    with foundation.uow.transaction() as tx:
        task = ledger.tasks.load(tx, job.job_id)[1]
        assert task.state == "attention_required"
        assert task.effect_status == "unknown"
    engine.close()


def test_queue_deadline_settles_without_contacting_broker(foundation, ledger):
    async def unused(step):
        pytest.fail("expired queued work cannot invoke a provider")

    engine = executor(ledger, unused)
    job, _ = admit(foundation, ledger)
    with foundation.uow.transaction() as tx:
        intent = tx.read("celery_dispatch_intents", job.job_id)
    ledger.tasks.clock = lambda: "2099-01-01T00:00:00.000Z"
    assert engine.expire(intent)
    with foundation.uow.transaction() as tx:
        assert ledger.tasks.load(tx, job.job_id)[1].state == "failed"
        assert tx.read("celery_dispatch_intents", job.job_id)["state"] == "terminal"
    engine.close()


def test_cancel_pending_completes_control_and_keeps_historical_binding(foundation, ledger):
    from aether_agent_memory.runtime.celery.controls import RoutedControls
    from aether_agent_memory.runtime.temporal.controls import ControlRequest

    async def unused(step):
        pytest.fail("cancelled work cannot invoke provider")

    engine = executor(ledger, unused)
    job, _ = admit(foundation, ledger)
    controls = RoutedControls(ledger, (), engine)
    ctx = foundation.identity.context("alice")
    with foundation.uow.transaction() as tx:
        task = ledger.tasks.load(tx, job.job_id)[1]
        operation = controls.task(
            tx,
            ctx,
            job.job_id,
            ControlRequest(
                operation_id="cancel",
                action="cancel",
                expected_revision=task.revision,
                reason="user requested",
            ),
        )
        assert operation.state == "completed"
        assert ledger.tasks.load(tx, job.job_id)[1].state == "cancelled"
        assert tx.read("temporal_bindings", job.job_id)["backend"] == "celery"
    engine.close()


@pytest.mark.asyncio
async def test_periodic_pause_is_authorized_and_blocks_batch_commit(foundation, ledger):
    from aether_agent_memory.runtime.celery.controls import RoutedControls
    from aether_agent_memory.runtime.foundation.common import FoundationError
    from aether_agent_memory.runtime.temporal.controls import ControlRequest
    from aether_agent_memory.runtime.temporal.models import PeriodicState
    from aether_agent_memory.runtime.temporal.periodic import PeriodicActivities
    async def unused(step):
        pytest.fail("periodic control cannot invoke stage")
    engine = executor(ledger, unused)
    controls = RoutedControls(ledger, ("alice",), engine)
    ctx = foundation.identity.context("alice")
    runner = PeriodicActivities(ledger, None)
    runner.transport = "celery"
    async def prepare(key):
        with foundation.uow.transaction() as tx:
            operation = controls.remember_periodic(tx, ctx, ControlRequest(
                operation_id="periodic_cancel", action="cancel", expected_revision=1,
                reason="pause scanner"))
            assert operation.state == "completed"
    def commit(tx, key, tick, prepared):
        pytest.fail("pause between prepare and commit must fence the batch")
    runner.register("remember_pending", commit, prepare)
    with foundation.uow.transaction() as tx:
        tx.write("remember_pending", "pending", {"state": "pending"})
    with pytest.raises(FoundationError, match="paused"):
        await runner.batch(PeriodicState(deployment_id="test", last_tick=1))
    with foundation.uow.transaction() as tx:
        assert controls.remember_periodic_snapshot(tx, ctx)["enabled"] is False
    engine.close()
