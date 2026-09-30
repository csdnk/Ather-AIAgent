import asyncio
import importlib
import importlib.util
from uuid import uuid4

import pytest
from test_ingress import runtime as runtime

from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
from aether_agent_memory.operate.contracts.models import Tier
from aether_agent_memory.remember.contracts.models import RememberRequest, SourceInput, TextInput
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import fingerprint, now
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.worker import WorkerHost


def setup(runtime):
    module = "aether_agent_memory.runtime.temporal.operate"
    assert importlib.util.find_spec(module), "Operate lacks stage routes"
    ledger = ExecutionLedger(
        runtime.foundation.tasks,
        TemporalConfiguration(deployment_id="test", endpoint="127.0.0.1:7233"),
    )
    maintenance = CacheMaintenance(runtime)
    registry = StageRegistry()
    importlib.import_module(module).register_operate(registry, runtime.operate, maintenance)
    return ledger, maintenance, registry


async def memory(runtime):
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    receipt = await runtime.remember.save(
        ctx,
        RememberRequest(
            selection=ScopeSelector(session_id="session"),
            source=SourceInput(
                kind="conversation",
                external_id=uuid4().hex,
                external_version="1",
                occurred_at=now(),
            ),
            content=TextInput(kind="text", text="Keep this verified original."),
        ),
    )
    # Seed all dependent workflows before testing the fault, independently of host load.
    await runtime.drain(timeout_seconds=60)
    return runtime.remember.get(ctx, receipt.memories[0].memory_id)


@pytest.mark.asyncio
async def test_prepared_actions_reserve_one_intent_before_any_submission(runtime, monkeypatch):
    from test_ledger import delivery

    from aether_agent_memory.runtime.temporal.models import StepRequest

    item = await memory(runtime)
    ledger, _, _ = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    tasks = []
    with runtime.foundation.uow.transaction() as tx:
        key = fingerprint([item.ref.scope.model_dump(mode="json"), item.ref.memory_id])
        view = tx.read("operate_views", key)
        tx.write("operate_views", key, {**view, "successful_reads": 1})
        for _ in range(2):
            task_id = runtime.operate.enqueue(
                tx, ctx, item.ref, uuid4().hex, cleanup=False, permanent=False
            )
            job = ledger.bind_admitted(tx, ledger.tasks.load(tx, task_id)[1])
            ledger.begin(
                tx, StepRequest(job=job, stage="prepare", ordinal=0, mode="execute"), delivery(job)
            )
            tasks.append(ledger.tasks.load(tx, task_id)[1])
    # Deterministic Workflow interleaving: both prepares precede either submit.
    prepared = [await runtime.operate.prepare_evaluation(ctx, task) for task in tasks]
    assert prepared[0]["intent"] == prepared[1]["intent"]
    submit = runtime.executor.submit
    calls = []

    async def counted(context, intent):
        calls.append(intent.action_id)
        return await submit(context, intent)

    monkeypatch.setattr(runtime.executor, "submit", counted)
    results = [
        await runtime.operate.submit_evaluation(ctx, task, data)
        for task, data in zip(tasks, prepared, strict=True)
    ]
    assert len(calls) == 1
    assert all(result["action_state"] == "succeeded" for result in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("effect", ["confirmed", "unknown", "revoked", "tampered"])
async def test_unknown_action_only_queries_original_id(
    runtime, workflow_client, monkeypatch, effect
):
    ledger, _, registry = setup(runtime)
    item = await memory(runtime)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    with runtime.foundation.uow.transaction() as tx:
        key = fingerprint([item.ref.scope.model_dump(mode="json"), item.ref.memory_id])
        view = tx.read("operate_views", key)
        tx.write("operate_views", key, {**view, "successful_reads": 1})
        task_id = runtime.operate.enqueue(
            tx, ctx, item.ref, uuid4().hex, cleanup=False, permanent=False
        )
        job = ledger.bind_admitted(tx, ledger.tasks.load(tx, task_id)[1])
    submit, query = runtime.executor.submit, runtime.executor.query
    submitted, queried_ids = [], []

    async def lose_response(context, intent):
        submitted.append(intent.action_id)
        if effect != "unknown":
            await submit(context, intent)
        if effect == "revoked":
            from contextvars import Context

            def revoke():
                with runtime.foundation.uow.transaction() as tx:
                    identity = tx.read("identities", "alice")
                    tx.write("identities", "alice", {**identity, "enabled": False})

            Context().run(revoke)
        raise OSError("lost action response")

    async def query_original(context, action_id):
        queried_ids.append(action_id)
        return await query(context, action_id)

    monkeypatch.setattr(runtime.executor, "submit", lose_response)
    monkeypatch.setattr(runtime.executor, "query", query_original)
    if effect == "tampered":
        evaluate = runtime.operate.submit_evaluation

        async def change_binding(context, task, prepared):
            result = await evaluate(context, task, prepared)
            with runtime.foundation.uow.transaction() as tx:
                action_id = prepared["intent"]["action_id"]
                row = tx.read("operate_actions", action_id)
                row["intent"]["content_hash"] = "0" * 64
                tx.write("operate_actions", action_id, row)
            return result

        monkeypatch.setattr(runtime.operate, "submit_evaluation", change_binding)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            task = ledger.tasks.load(tx, job.job_id)[1]
            original_action_id = tx.read("operate_task_actions", task.task_id)["action_id"]
            if effect == "confirmed":
                assert task.state == "succeeded", task
                assert tx.get(task.result_ref)["action_id"] == original_action_id
                assert tx.read("operate_actions", original_action_id)["state"] == "succeeded"
            else:
                assert task.state == "attention_required", task
                assert tx.read("operate_actions", original_action_id)["state"] != "succeeded"
        submit_count = len(submitted)
        assert submit_count == 1
        assert set(queried_ids) == (
            set() if effect in {"revoked", "tampered"} else {original_action_id}
        )
        assert len(queried_ids) <= 6
    finally:
        await host.stop()


async def repair_job(runtime, ledger, maintenance):
    item = await memory(runtime)
    with runtime.executor.db() as db:
        tier = db.execute(
            "SELECT tier FROM copies WHERE key=?", (runtime.executor.key(item.ref),)
        ).fetchone()[0]
    path = runtime.executor.path(item.ref, Tier(tier))
    original = path.read_bytes()
    path.write_bytes(b"corrupt")
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    samples = await maintenance.sample(ctx)
    incidents = [
        incident
        for subject, sample in samples
        for incident in runtime.foundation.dispositions.observe(ctx, subject, sample)
    ]
    incident = next(i for i in incidents if i.subject.object_id == runtime.executor.key(item.ref))
    with runtime.foundation.uow.transaction() as tx:
        task_id = tx.read("incidents", incident.incident_id)["task_id"]
        job = ledger.bind_admitted(tx, ledger.tasks.load(tx, task_id)[1])
    return job, incident, path, original


@pytest.mark.asyncio
async def test_repair_without_verification_does_not_resolve(runtime, workflow_client):
    ledger, maintenance, registry = setup(runtime)
    job, incident, path, original = await repair_job(runtime, ledger, maintenance)

    async def unavailable(*args):
        return ()

    runtime.foundation.dispositions.verifiers[maintenance.rule.verification_operation] = unavailable
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        assert path.read_bytes() == original
        with runtime.foundation.uow.transaction() as tx:
            incident = tx.read("incidents", incident.incident_id)["record"]
            assert incident["state"] != "resolved"
            assert incident["verification"] != "passed"
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_cache_repair_ack_loss_is_read_back_before_resolution(
    runtime, workflow_client, monkeypatch
):
    ledger, maintenance, registry = setup(runtime)
    job, incident, path, original = await repair_job(runtime, ledger, maintenance)
    repair = runtime.executor.repair
    calls = []

    def interrupted(item, operation_id):
        calls.append(operation_id)
        repair(item, operation_id)
        raise OSError("lost repair confirmation")

    monkeypatch.setattr(runtime.executor, "repair", interrupted)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        assert path.read_bytes() == original
        assert calls == [job.job_id]
        with runtime.foundation.uow.transaction() as tx:
            current = tx.read("incidents", incident.incident_id)["record"]
            assert current["state"] == "resolved"
            assert current["verification"] == "passed"
            assert current["verification_refs"]
            assert ledger.tasks.load(tx, job.job_id)[1].state == "succeeded"
    finally:
        await host.stop()
