"""Exercise real SDK dispatch, business fences and bounded recovery policies."""

import asyncio
import threading
from contextvars import Context
from uuid import uuid4

import pytest
from temporalio.worker import Replayer
from test_ledger import admit, delivery
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger

from aether_agent_memory.runtime.contracts.models import EffectStatus, Permission, TaskState
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.temporal.activities import StageContext
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.models import ControlIntent, StepRequest, StepResult
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.worker import WorkerHost
from aether_agent_memory.runtime.temporal.workflows import P3TaskWorkflow


async def launch(app, ledger, registry, client):
    job, spec = admit(app, ledger, uuid4().hex)
    host = WorkerHost(client, ledger, registry)
    await host.start()
    await IntentBridge(ledger, TemporalGateway(client, ledger)).flush()
    return host, client.get_workflow_handle(delivery(job).workflow_id, result_type=StepResult), spec


def query(reason="uncertain"):
    return StepResult(outcome="query", effect_status=EffectStatus.UNKNOWN, reason_code=reason)


@pytest.mark.asyncio
async def test_timed_out_write_enters_reconcile_before_execute(foundation, ledger, workflow_client):
    calls = []

    async def execute(step):
        calls.append("execute")
        await asyncio.sleep(1)
        return query()

    async def reconcile(step):
        calls.append("reconcile")
        return query()

    registry = StageRegistry()
    registry.register(
        "engineering.save",
        "write",
        execute,
        reconcile,
        permission=Permission.WRITE,
        effect_mode="uncertain",
        timeout_seconds=0.1,
    )
    host, handle, _ = await launch(foundation, ledger, registry, workflow_client)
    try:
        result = await asyncio.wait_for(handle.result(), 60)
        assert result.outcome == "attention"
        assert calls[:2] == ["execute", "reconcile"]
        assert calls.count("execute") == 1
        assert calls.count("reconcile") <= 6
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_budget_is_not_multiplied_by_sdk_retry(foundation, ledger, workflow_client):
    calls = []

    async def execute(step):
        calls.append("execute")
        return StepResult(outcome="retry", effect_status=EffectStatus.NO_EFFECT, reason_code="busy")

    async def reconcile(step):
        calls.append("reconcile")
        return query()

    registry = StageRegistry()
    registry.register(
        "engineering.save",
        "write",
        execute,
        reconcile,
        permission=Permission.WRITE,
        effect_mode="uncertain",
    )
    host, handle, spec = await launch(foundation, ledger, registry, workflow_client)
    try:
        result = await asyncio.wait_for(handle.result(), 60)
        assert result.outcome in {"failed", "attention"}
        assert calls.count("execute") == 3
        with foundation.uow.transaction() as tx:
            assert foundation.tasks.load(tx, spec.task_id)[1].attempt == 3
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_revoked_identity_rejects_late_commit(foundation, ledger, workflow_client):
    rejected = []

    async def execute(step):
        context = StageContext.current()

        def revoke_from_control_plane():
            with foundation.uow.transaction() as tx:
                identity = tx.read("identities", "alice")
                tx.write("identities", "alice", {**identity, "enabled": False})

        Context().run(revoke_from_control_plane)
        try:
            with foundation.uow.transaction() as tx:
                context.guard(tx)
        except FoundationError as exc:
            rejected.append(exc.code.value)
            raise
        pytest.fail("revoked principal committed")

    async def reconcile(step):
        pytest.fail("revoked principal must not call provider")

    registry = StageRegistry()
    registry.register(
        "engineering.save",
        "write",
        execute,
        reconcile,
        permission=Permission.WRITE,
        effect_mode="uncertain",
    )
    host, handle, spec = await launch(foundation, ledger, registry, workflow_client)
    try:
        result = await asyncio.wait_for(handle.result(), 60)
        assert rejected == ["FORBIDDEN"]
        assert result.outcome == "attention"
        with foundation.uow.transaction() as tx:
            assert foundation.tasks.load(tx, spec.task_id)[1].state == TaskState.ATTENTION
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_activity_failure_history_has_no_secret_payload(foundation, ledger, workflow_client):
    secret = "provider-secret-" + uuid4().hex

    async def execute(step):
        raise ValueError(secret)

    async def reconcile(step):
        return StepResult(
            outcome="attention", effect_status=EffectStatus.UNKNOWN, reason_code="query_required"
        )

    registry = StageRegistry()
    registry.register(
        "engineering.save",
        "write",
        execute,
        reconcile,
        permission=Permission.WRITE,
        effect_mode="uncertain",
    )
    host, handle, _ = await launch(foundation, ledger, registry, workflow_client)
    try:
        assert (await asyncio.wait_for(handle.result(), 60)).outcome == "attention"
        history = await handle.fetch_history()
        assert secret not in history.to_json()
        await Replayer(
            workflows=[P3TaskWorkflow], data_converter=workflow_client.data_converter
        ).replay_workflow(history)
    finally:
        await host.stop()


def test_late_delivery_cannot_reacquire_after_next_stage(foundation, ledger):
    job, _ = admit(foundation, ledger, uuid4().hex)
    first = StepRequest(job=job, stage="first", ordinal=0, mode="execute")
    with foundation.uow.transaction() as tx:
        execution = ledger.begin(tx, first, delivery(job))
        ledger.save_step(
            tx,
            first,
            execution,
            StepResult(
                outcome="done",
                next_stage="second",
                effect_status=EffectStatus.NO_EFFECT,
                reason_code="prepared",
            ),
        )
    with foundation.uow.transaction() as tx:
        ledger.begin(
            tx,
            first.model_copy(update={"stage": "second", "ordinal": 1}),
            delivery(job).model_copy(update={"activity_id": "2"}),
        )
    with pytest.raises(FoundationError), foundation.uow.transaction() as tx:
        ledger.begin(tx, first, delivery(job))


@pytest.mark.parametrize("mutate_input", [True, False])
def test_cached_step_requires_intact_input_and_output(foundation, ledger, mutate_input):
    job, spec = admit(foundation, ledger, uuid4().hex)
    step = StepRequest(job=job, stage="first", ordinal=0, mode="execute")
    output = spec.subject.model_copy(update={"object_type": "output"})
    with foundation.uow.transaction() as tx:
        execution = ledger.begin(tx, step, delivery(job))
        tx.put_if_revision(output, {"value": 7}, None)
        ledger.save_step(
            tx,
            step,
            execution,
            StepResult(
                outcome="done",
                result_ref=output,
                next_stage="second",
                effect_status=EffectStatus.CONFIRMED,
                reason_code="saved",
            ),
        )
    with foundation.uow.transaction() as tx:
        target = spec.input_ref if mutate_input else output
        tx.put_if_revision(target, {"value": "changed"}, tx.revision(target))
    with pytest.raises(FoundationError), foundation.uow.transaction() as tx:
        ledger.load_step(tx, step)


@pytest.mark.asyncio
async def test_shutdown_finishes_inflight_work(foundation, ledger, workflow_client):
    entered = asyncio.Event()

    async def execute(step):
        entered.set()
        await asyncio.sleep(0.15)
        context = StageContext.current()
        output = context.task.subject.model_copy(update={"object_type": "output"})
        with foundation.uow.transaction() as tx:
            context.guard(tx)
            tx.put_if_revision(output, {"value": 7}, None)
        return StepResult(
            outcome="done",
            result_ref=output,
            effect_status=EffectStatus.CONFIRMED,
            reason_code="saved",
        )

    registry = StageRegistry()
    registry.register(
        "engineering.save",
        "save",
        execute,
        execute,
        permission=Permission.WRITE,
        effect_mode="idempotent",
    )
    host, handle, spec = await launch(foundation, ledger, registry, workflow_client)
    await asyncio.wait_for(entered.wait(), 60)
    await host.stop()
    with foundation.uow.transaction() as tx:
        assert foundation.tasks.load(tx, spec.task_id)[1].state == TaskState.SUCCEEDED
    restarted = WorkerHost(workflow_client, ledger, registry)
    await restarted.start()
    try:
        assert (await asyncio.wait_for(handle.result(), 60)).outcome == "done"
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_duplicate_cancel_controls_never_repeat_external_write(
    foundation, ledger, workflow_client
):
    entered = asyncio.Event()
    calls = []

    async def execute(step):
        calls.append("write")
        entered.set()
        await asyncio.sleep(2)
        return query()

    async def reconcile(step):
        calls.append("query")
        return StepResult(
            outcome="attention",
            effect_status=EffectStatus.UNKNOWN,
            reason_code="original_action_unknown",
        )

    registry = StageRegistry()
    registry.register(
        "engineering.save",
        "save",
        execute,
        reconcile,
        permission=Permission.WRITE,
        effect_mode="uncertain",
    )
    host, handle, spec = await launch(foundation, ledger, registry, workflow_client)
    try:
        await asyncio.wait_for(entered.wait(), 60)
        control = ControlIntent(
            control_id="cancel", job_id=spec.task_id, action="cancel", expected_revision=1
        )
        await handle.signal("control", control)
        await handle.signal("control", control)
        assert (await asyncio.wait_for(handle.result(), 60)).outcome == "attention"
        state = await handle.query("state")
        assert state["acknowledged_controls"] == ["cancel"]
        assert calls == ["write", "query"]
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_timed_out_old_activity_cannot_close_new_success(foundation, ledger, workflow_client):
    late_finished = asyncio.Event()
    release_old = threading.Event()
    loop = asyncio.get_running_loop()
    calls = []
    rejected = []

    async def execute(step):
        context = StageContext.current()
        calls.append("execute")
        if calls.count("execute") == 1:

            def stale_provider_completion():
                assert release_old.wait(6)
                try:
                    with foundation.uow.transaction() as tx:
                        context.guard(tx)
                except FoundationError as exc:
                    rejected.append(exc.code.value)
                finally:
                    loop.call_soon_threadsafe(late_finished.set)

            await context.blocking(stale_provider_completion)
        output = context.task.subject.model_copy(update={"object_type": "output"})
        with foundation.uow.transaction() as tx:
            context.guard(tx)
            tx.put_if_revision(output, {"winner": context.execution.epoch}, None)
        release_old.set()
        return StepResult(
            outcome="done",
            result_ref=output,
            effect_status=EffectStatus.CONFIRMED,
            reason_code="saved",
        )

    async def reconcile(step):
        calls.append("query")
        return StepResult(
            outcome="retry", effect_status=EffectStatus.NO_EFFECT, reason_code="verified_absent"
        )

    foundation.tasks.retry_seconds = 0.01
    registry = StageRegistry()
    registry.register(
        "engineering.save",
        "save",
        execute,
        reconcile,
        permission=Permission.WRITE,
        effect_mode="uncertain",
        timeout_seconds=0.1,
    )
    host, handle, spec = await launch(foundation, ledger, registry, workflow_client)
    try:
        assert (await asyncio.wait_for(handle.result(), 60)).outcome == "done"
        await asyncio.wait_for(late_finished.wait(), 60)
        with foundation.uow.transaction() as tx:
            assert foundation.tasks.load(tx, spec.task_id)[1].state == TaskState.SUCCEEDED
        assert calls[:3] == ["execute", "query", "execute"]
        assert rejected == ["VERSION_CONFLICT"]
    finally:
        release_old.set()
        await host.stop()
