import asyncio
import importlib
import importlib.util
from uuid import uuid4

import pytest
from temporalio.worker import Replayer, Worker
from test_ledger import admit, delivery
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger

from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import EffectStatus, TaskState
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.models import ControlIntent, StepRequest, WorkflowBinding


def module():
    name = "aether_agent_memory.runtime.temporal.periodic"
    assert importlib.util.find_spec(name), "no durable periodic cursor"
    return importlib.import_module(name)


@pytest.mark.asyncio
async def test_partial_periodic_batch_resumes_without_skipping(foundation, ledger):
    api = module()
    with foundation.uow.transaction() as tx:
        for key in ("first", "second"):
            tx.write("due", key, {})
    fail = True

    def execute(tx, key, tick, prepared):
        if key == "second" and fail:
            raise OSError("interrupt between committed items")
        tx.write("counts", key, (tx.read("counts", key) or 0) + 1)

    runner = api.PeriodicActivities(ledger, None)
    runner.register("due", execute)
    state = api.PeriodicState(deployment_id="test", last_tick=10, cursor="", batch_size=2)
    with pytest.raises(OSError):
        await runner.run_periodic_batch(state)
    with foundation.uow.transaction() as tx:
        assert dict(tx.rows("counts")) == {"first": 1}
    fail = False
    resumed = await runner.run_periodic_batch(state)
    while resumed.cursor is not None:
        resumed = await runner.run_periodic_batch(resumed)
    await runner.run_periodic_batch(state)  # lost batch response and stale caller state
    with foundation.uow.transaction() as tx:
        counts = dict(tx.rows("counts"))
        assert counts == {"first": 1, "second": 1}


@pytest.mark.asyncio
async def test_continue_as_new_preserves_pending_controls(foundation, ledger, workflow_client):
    api = module()
    runner = api.PeriodicActivities(ledger, TemporalGateway(workflow_client, ledger))
    queue, workflow_id = "periodic-" + uuid4().hex, "periodic-" + uuid4().hex
    state = api.PeriodicState(deployment_id="test", interval_seconds=0.2, continue_after=1)
    async with Worker(
        workflow_client,
        task_queue=queue,
        workflows=[api.P3PeriodicWorkflow],
        activities=[runner.run_periodic_batch],
    ):
        handle = await workflow_client.start_workflow(
            api.P3PeriodicWorkflow.run, state, id=workflow_id, task_queue=queue
        )
        control = ControlIntent(
            control_id="same-control", job_id="test", action="reconcile", expected_revision=1
        )
        await handle.signal("control", control)
        first = handle.first_execution_run_id
        binding = WorkflowBinding(
            namespace=workflow_client.namespace,
            workflow_id=workflow_id,
            first_run_id=first,
            current_run_id=first,
            input_hash="0" * 64,
            plan_version="1",
        )
        async with asyncio.timeout(60):
            while (
                await workflow_client.get_workflow_handle(workflow_id).describe()
            ).run_id == first:
                await asyncio.sleep(0.05)
        await handle.signal("control", control)
        status = await TemporalGateway(workflow_client, ledger).describe(binding)
        assert status.state == "running" and status.run_id != first
        current = await handle.query("state")
        assert current["acknowledged_controls"] == ["same-control"]
        await Replayer(
            workflows=[api.P3PeriodicWorkflow], data_converter=workflow_client.data_converter
        ).replay_workflow(
            await workflow_client.get_workflow_handle(workflow_id, run_id=first).fetch_history()
        )
        await handle.signal(
            "control",
            ControlIntent(control_id="cancel", job_id="test", action="cancel", expected_revision=1),
        )
        await asyncio.wait_for(handle.result(), 60)


@pytest.mark.asyncio
async def test_terminated_workflow_does_not_hide_unknown_effect(
    foundation, ledger, workflow_client
):
    api = module()
    job, _ = admit(foundation, ledger, uuid4().hex)
    gateway = TemporalGateway(workflow_client, ledger)
    await IntentBridge(ledger, gateway).flush()
    with foundation.uow.transaction() as tx:
        binding = WorkflowBinding.model_validate(
            tx.read("temporal_bindings", job.job_id)["binding"]
        )
        ledger.begin(
            tx,
            StepRequest(job=job, stage="save", ordinal=0, mode="execute"),
            delivery(job).model_copy(update={"run_id": binding.current_run_id}),
        )
        row, task = ledger.tasks.load(tx, job.job_id)
        ledger.tasks.change(
            tx, row, task, state=TaskState.RECOVERY_WAIT, effect_status=EffectStatus.UNKNOWN
        )
        binding = WorkflowBinding.model_validate(
            tx.read("temporal_bindings", job.job_id)["binding"]
        )
    await workflow_client.get_workflow_handle(binding.workflow_id).terminate()
    status = await api.PeriodicActivities(ledger, gateway).reconcile_execution_status(binding)
    assert status.state == "terminated"
    with foundation.uow.transaction() as tx:
        _, task = ledger.tasks.load(tx, job.job_id)
        assert task.effect_status == EffectStatus.UNKNOWN
        assert task.state != TaskState.FAILED
        assert (
            tx.read("temporal_diagnostics", binding.workflow_id)["reason_code"]
            == "TECHNICAL_END_WITH_UNKNOWN_EFFECT"
        )


@pytest.mark.asyncio
async def test_p3_periodic_routes_keep_due_items_and_exclude_external_repair(periodic_runtime):
    runtime = periodic_runtime
    from test_operate_workflow import memory

    from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.gateway import connect_client
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger

    api = module()
    item = await memory(runtime)
    ledger = ExecutionLedger(
        runtime.foundation.tasks,
        TemporalConfiguration(deployment_id="test", endpoint=runtime.execution.endpoint),
    )
    maintenance = CacheMaintenance(runtime)
    client = await connect_client(ledger.config)
    runner = api.PeriodicActivities(ledger, TemporalGateway(client, ledger))
    assert hasattr(api, "register_p3_periodic"), "domain due-item boundaries are not wired"
    api.register_p3_periodic(runner, runtime, maintenance, ("alice",))
    assert set(runner.routes) == {
        "remember_pending",
        "remember_retention_enrollment",
        "remember_reflection_policies",
        "operate_views",
        "temporal_maintenance_principals",
        "temporal_bindings",
    }
    copy = next((copy for copy in runtime.executor.copies() if copy.memory == item.ref), None)
    # One Redis digest can back distinct Working and episodic memory copies.
    affected = {
        copy.memory.model_dump_json()
        for copy in runtime.executor.copies()
        if copy.memory.scope == item.ref.scope and copy.content_hash == item.content_hash
    }
    assert item.ref.model_dump_json() in affected and len(affected) == 2
    assert copy is not None
    cache = runtime.executor.cache
    key, field, _ = cache.keys(item.ref.scope, copy.content_hash)
    cache.client.hset(key, field, b"corrupt")
    runtime.foundation.tasks.on_admitted = ledger.bind_admitted
    commit, prepare = runner.routes["temporal_maintenance_principals"]

    def interrupted(tx, key, tick, prepared):
        commit(tx, key, tick, prepared)
        raise OSError("crash before observations and cursor commit")

    runner.routes["temporal_maintenance_principals"] = interrupted, prepare
    state = api.PeriodicState(deployment_id="test", last_tick=123, cursor="", batch_size=1)
    with pytest.raises(OSError):
        while state.cursor is not None:
            state = await runner.run_periodic_batch(state)
    with runtime.foundation.uow.transaction() as tx:
        assert tx.rows("cache_sample_cursors") == []
        assert tx.rows("signal_samples") == []
        assert tx.rows("incidents") == []
    runner.routes["temporal_maintenance_principals"] = commit, prepare
    while state.cursor is not None:
        state = await runner.run_periodic_batch(state)
    with runtime.foundation.uow.transaction() as tx:
        assert tx.rows("temporal_ticks")
        if copy:
            repairs = [
                row
                for _, row in tx.rows("tasks")
                if row["record"]["kind"] == "operate_repair_cache"
            ]
            assert len(repairs) == len(affected)
            targets = {row["record"]["subject"]["object_id"] for row in repairs}
            assert targets == {
                runtime.executor.key(MemoryRef.model_validate_json(ref)) for ref in affected
            }


@pytest.mark.asyncio
async def test_cache_sampling_skips_early_ticks_without_delaying_due_checks(
    periodic_runtime, monkeypatch
):
    from test_operate_workflow import memory

    from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
    from aether_agent_memory.runtime.foundation.common import later, now
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.gateway import connect_client
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger

    runtime = periodic_runtime
    item = await memory(runtime)
    api = module()
    ledger = ExecutionLedger(
        runtime.foundation.tasks,
        TemporalConfiguration(deployment_id="test", endpoint=runtime.execution.endpoint),
    )
    maintenance = CacheMaintenance(runtime)
    client = await connect_client(ledger.config)
    runner = api.PeriodicActivities(ledger, TemporalGateway(client, ledger))
    api.register_p3_periodic(runner, runtime, maintenance, ("alice",))
    runtime.foundation.tasks.on_admitted = ledger.bind_admitted
    affected = {
        copy.key
        for copy in runtime.executor.copies()
        if copy.memory.scope == item.ref.scope and copy.content_hash == item.content_hash
    }
    assert runtime.executor.key(item.ref) in affected and len(affected) == 2
    started = stamp = now()
    monkeypatch.setattr(runtime.foundation.tasks, "clock", lambda: stamp)

    async def tick(number):
        state = api.PeriodicState(
            deployment_id="test", last_tick=number, interval_seconds=1, cursor="", batch_size=1
        )
        while state.cursor is not None:
            state = await runner.run_periodic_batch(state)

    await tick(0)
    with runtime.foundation.uow.transaction() as tx:
        initial = dict(tx.rows("signal_samples"))
        initial_cursors = dict(tx.rows("cache_sample_cursors"))
        sample_id = next(
            key
            for key, row in initial.items()
            if row["subject"]["object_id"] == runtime.executor.key(item.ref)
        )
        assert initial[sample_id]["observation"]["value"] == "healthy"
        assert tx.rows("incidents") == []

    cache = runtime.executor.cache
    key, field, _ = cache.keys(item.ref.scope, item.content_hash)
    cache.client.hset(key, field, b"corrupt")

    # A one-second periodic tick must not submit a five-second signal early.
    for number, elapsed in ((1, 1), (2, 4.999)):
        stamp = later(started, elapsed)
        await tick(number)
        await tick(number)  # A lost batch response must remain safe to retry.
        with runtime.foundation.uow.transaction() as tx:
            assert dict(tx.rows("signal_samples")) == initial
            assert dict(tx.rows("cache_sample_cursors")) == initial_cursors
            assert tx.rows("incidents") == []

    # The exact boundary is due; skipping early ticks must not disable repair.
    stamp = later(started, 5)
    await tick(3)
    with runtime.foundation.uow.transaction() as tx:
        samples = dict(tx.rows("signal_samples"))
        assert samples.keys() == initial.keys()
        observation = samples[sample_id]["observation"]
        assert observation["observed_at"] == stamp
        assert observation["value"] == "corrupt"
        incidents = tx.rows("incidents")
        assert len(incidents) == len(affected)
        assert {row["record"]["subject"]["object_id"] for _, row in incidents} == affected
        assert all(row["record"]["state"] == "recovering" for _, row in incidents)


@pytest.mark.asyncio
async def test_cache_sampler_reuses_persisted_observations_on_fresh_host(
    periodic_runtime, tmp_path, monkeypatch
):
    from test_operate_workflow import memory

    from aether_agent_memory.operate.basic.continuous import ContinuousOperate
    from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
    from aether_agent_memory.runtime.foundation.common import later, now
    from azure_test_runtime import create_runtime

    runtime = periodic_runtime
    await memory(runtime)
    stamp = now()
    monkeypatch.setattr(runtime.foundation.tasks, "clock", lambda: stamp)
    maintenance = CacheMaintenance(runtime)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    observations, _, _ = await maintenance.sample_batch(ctx)
    assert observations
    for subject, observation in observations:
        runtime.foundation.dispositions.observe(ctx, subject, observation)

    reopened = create_runtime(
        tmp_path / "p3.db",
        tmp_path / "cache",
        embedding_profile="injected",
        operate_factory=ContinuousOperate,
    )
    try:
        monkeypatch.setattr(reopened.foundation.tasks, "clock", lambda: later(stamp, 1))
        sampler = CacheMaintenance(reopened)
        context = reopened.foundation.identity.context("alice", timeout_seconds=60)
        samples, _, cursor = await sampler.sample_batch(context)
        assert samples == []
        assert cursor  # Skipped rows still count toward the bounded scan.
    finally:
        reopened.close()


@pytest.fixture
def periodic_runtime(tmp_path, temporal_server):
    from hashlib import sha256

    from aether_agent_memory.operate.basic.continuous import ContinuousOperate
    from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
    from azure_test_runtime import create_runtime

    app = create_runtime(
        tmp_path / "p3.db",
        tmp_path / "cache",
        embedding_profile="injected",
        operate_factory=ContinuousOperate,
    )
    principal = Principal(
        principal_id="alice",
        auth_epoch=1,
        permissions=tuple(Permission),
        home_scope=Scope(
            tenant_id="tenant", application_id="app", user_id="alice", agent_id="agent"
        ),
    )
    app.foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    from temporal_test_support import seed_driver

    seed_driver(app, temporal_server)
    yield app
    app.close()


@pytest.mark.asyncio
async def test_periodic_cache_sampling_keeps_signal_cadence_across_operators(
    periodic_runtime, monkeypatch
):
    from hashlib import sha256

    from test_operate_workflow import memory

    from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
    from aether_agent_memory.runtime.contracts.models import ErrorCode
    from aether_agent_memory.runtime.foundation.common import (
        FoundationError,
        fingerprint,
        later,
        now,
    )
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger

    runtime = periodic_runtime
    await memory(runtime)
    stamp = now()
    monkeypatch.setattr(runtime.foundation.tasks, "clock", lambda: stamp)
    alice = runtime.foundation.identity.context("alice").principal
    bob = alice.model_copy(update={"principal_id": "bob"})
    runtime.foundation.identity.provision(
        [(sha256(b"alice").hexdigest(), alice), (sha256(b"bob").hexdigest(), bob)]
    )
    ledger = ExecutionLedger(
        runtime.foundation.tasks,
        TemporalConfiguration(deployment_id="test", endpoint=runtime.execution.endpoint),
    )
    runner = module().PeriodicActivities(ledger, None)
    maintenance = CacheMaintenance(runtime)
    module().register_p3_periodic(runner, runtime, maintenance, ("alice", "bob"))
    commit, prepare = runner.routes["temporal_maintenance_principals"]

    prepared = await prepare("alice")
    # Both real prepares precede the first commit. Only committed samples can
    # throttle prepare, while commit must reject a competing early refresh.
    stamp = later(stamp, 1)
    bob_prepared = await prepare("bob")
    with runtime.foundation.uow.transaction() as tx:
        commit(tx, "alice", 1, prepared)
        samples = tx.rows("signal_samples")
    assert samples, "the seeded cache must actually be sampled"
    baseline = {key: row["observation"]["observed_at"] for key, row in samples}
    baseline_subjects = {fingerprint(row["subject"]) for _, row in samples}
    ctx, (observations, cursor_key, cursor) = prepared
    subject, observation = observations[0]
    conflicting = observation.model_copy(update={"value": "corrupt"})
    with pytest.raises(FoundationError) as error, runtime.foundation.uow.transaction() as tx:
        commit(tx, "alice", 1, (ctx, ([(subject, conflicting)], cursor_key, cursor)))
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    prepared = bob_prepared
    assert prepared[1][0], "the second operator must actually inspect the shared cache"
    assert baseline_subjects & {
        fingerprint(subject.model_dump(mode="json")) for subject, _ in prepared[1][0]
    }
    with runtime.foundation.uow.transaction() as tx:
        commit(tx, "bob", 2, prepared)
        assert {
            key: row["observation"]["observed_at"] for key, row in tx.rows("signal_samples")
        } == baseline

    runner.routes = {"temporal_maintenance_principals": (commit, prepare)}
    completed = await runner.run_periodic_batch(
        module().PeriodicState(deployment_id="test", last_tick=2, batch_size=16)
    )
    assert completed.cursor is None
    with runtime.foundation.uow.transaction() as tx:
        assert tx.read("temporal_ticks", fingerprint(["test", 2]))["cursor"] is None
        assert {
            key: row["observation"]["observed_at"] for key, row in tx.rows("signal_samples")
        } == baseline

    stamp = later(stamp, 4)
    prepared = await prepare("alice")
    with runtime.foundation.uow.transaction() as tx:
        commit(tx, "alice", 3, prepared)
        assert all(
            row["observation"]["observed_at"] == stamp for _, row in tx.rows("signal_samples")
        )
        assert tx.rows("cache_sample_cursors")


@pytest.mark.asyncio
async def test_completed_business_with_terminated_run_is_visible(
    foundation, ledger, workflow_client
):
    api = module()
    job, spec = admit(foundation, ledger, uuid4().hex)
    gateway = TemporalGateway(workflow_client, ledger)
    await IntentBridge(ledger, gateway).flush()
    with foundation.uow.transaction() as tx:
        binding = WorkflowBinding.model_validate(
            tx.read("temporal_bindings", job.job_id)["binding"]
        )
        execution = ledger.begin(
            tx,
            StepRequest(job=job, stage="save", ordinal=0, mode="execute"),
            delivery(job).model_copy(update={"run_id": binding.current_run_id}),
        )
        result = spec.subject.model_copy(update={"object_type": "output"})
        tx.put_if_revision(result, {"ok": True}, None)
        ledger.complete(tx, job.job_id, execution, result)
    await workflow_client.get_workflow_handle(binding.workflow_id).terminate()
    await api.PeriodicActivities(ledger, gateway).reconcile_execution_status(binding)
    with foundation.uow.transaction() as tx:
        assert (
            tx.read("temporal_diagnostics", binding.workflow_id)["reason_code"]
            == "BUSINESS_COMPLETED_TECHNICAL_END"
        )
        assert ledger.tasks.load(tx, job.job_id)[1].state == TaskState.SUCCEEDED


@pytest.mark.asyncio
async def test_periodic_start_retains_chain_and_refuses_closed_restart(
    foundation, ledger, workflow_client
):
    from aether_agent_memory.runtime.foundation.common import FoundationError

    api = module()
    assert hasattr(api, "PeriodicController"), "no durable periodic start binding"
    controller = api.PeriodicController(TemporalGateway(workflow_client, ledger))
    runner = api.PeriodicActivities(ledger, controller.gateway)
    state = api.PeriodicState(deployment_id="test", interval_seconds=0.2, continue_after=1)
    async with Worker(
        workflow_client,
        task_queue="p3.periodic",
        workflows=[api.P3PeriodicWorkflow],
        activities=[runner.run_periodic_batch],
    ):
        binding = await controller.start(state)
        async with asyncio.timeout(60):
            while (await controller.gateway.describe(binding)).run_id == binding.current_run_id:
                await asyncio.sleep(0.05)
        resumed = await controller.start(state)
        assert resumed.first_run_id == binding.first_run_id
        assert resumed.current_run_id != binding.current_run_id
        await workflow_client.get_workflow_handle(binding.workflow_id).terminate()
        with pytest.raises(FoundationError):
            await controller.start(state)
