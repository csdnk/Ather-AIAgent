import asyncio
import importlib
import importlib.util
from uuid import uuid4

import pytest
from test_ingress import runtime as runtime

from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
from aether_agent_memory.operate.contracts.models import ActionIntent
from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    DeleteRequest,
    RememberRequest,
    SourceInput,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import fingerprint, now
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.worker import WorkerHost
from azure_operate_support import reserve_existing_intent


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


def seed_real_preparation(runtime, item, monkeypatch):
    """Reserve only after the real Activity establishes its execution fence."""
    prepare = runtime.operate.prepare_evaluation

    async def seed(context, task):
        await reserve_existing_intent(runtime, [task], item)
        return await prepare(context, task)

    monkeypatch.setattr(runtime.operate, "prepare_evaluation", seed)


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
    await reserve_existing_intent(runtime, tasks, item)
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
    assert all(result["action_state"] == "failed" for result in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("effect", ["confirmed", "unknown", "revoked", "tampered", "deleted"])
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
    seed_real_preparation(runtime, item, monkeypatch)
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
        if effect == "deleted":
            from contextvars import Context

            Context().run(
                runtime.remember.delete,
                runtime.foundation.identity.context("alice", operation_id=uuid4().hex),
                item.ref.memory_id,
                DeleteRequest(expected_revision=item.object_revision, reason="concurrent deletion"),
            )
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
            if effect in {"confirmed", "deleted"}:
                assert task.state == "succeeded", task
                assert tx.get(task.result_ref)["action_id"] == original_action_id
                action = tx.read("operate_actions", original_action_id)
                assert action["state"] == ("cancelled" if effect == "deleted" else "failed")
                assert action["feedback"]["reason"] == "unsupported_tier_transition"
                assert action["feedback"]["read_proof"] is None
                if effect == "deleted":
                    assert action["cleanup_state"] == "pending"
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


@pytest.mark.parametrize("change", ["delete", "correct"])
async def test_invalidated_prepared_action_closes_original_workflow_without_submission(
    runtime, workflow_client, monkeypatch, change
):
    item = await memory(runtime)
    ledger, _, registry = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", operation_id=uuid4().hex, timeout_seconds=60)
    with runtime.foundation.uow.transaction() as tx:
        key = fingerprint([item.ref.scope.model_dump(mode="json"), item.ref.memory_id])
        view = tx.read("operate_views", key)
        tx.write("operate_views", key, {**view, "successful_reads": 1})
        task_id = runtime.operate.enqueue(
            tx, ctx, item.ref, uuid4().hex, cleanup=False, permanent=False
        )
        job = ledger.bind_admitted(tx, ledger.tasks.load(tx, task_id)[1])
    seed_real_preparation(runtime, item, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    evaluate, submit, query = (
        runtime.operate.submit_evaluation,
        runtime.executor.submit,
        runtime.executor.query,
    )
    prepared, submissions, queries = [], [], []

    async def wait_after_prepare(context, task, data):
        prepared.append(ActionIntent.model_validate(data["intent"]))
        entered.set()
        await release.wait()
        return await evaluate(context, task, data)

    async def counted_submit(context, intent):
        submissions.append(intent.action_id)
        return await submit(context, intent)

    async def counted_query(context, action_id):
        queries.append(action_id)
        return await query(context, action_id)

    monkeypatch.setattr(runtime.operate, "submit_evaluation", wait_after_prepare)
    monkeypatch.setattr(runtime.executor, "submit", counted_submit)
    monkeypatch.setattr(runtime.executor, "query", counted_query)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(entered.wait(), 30)
        if change == "delete":
            runtime.remember.delete(
                ctx,
                item.ref.memory_id,
                DeleteRequest(
                    expected_revision=item.object_revision,
                    reason="delete before cache action",
                ),
            )
        else:
            await runtime.remember.correct_async(
                ctx,
                item.ref.memory_id,
                CorrectionRequest(
                    expected_version=item.ref.version,
                    content="A corrected original.",
                    source=SourceInput(
                        kind="conversation",
                        external_id=uuid4().hex,
                        external_version="1",
                        occurred_at=now(),
                    ),
                    reason="correct before cache action",
                ),
            )
        release.set()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            task = ledger.tasks.load(tx, job.job_id)[1]
            assert task.state == "succeeded", task
            action = tx.read("operate_actions", prepared[0].action_id)
            assert action["intent"] == prepared[0].model_dump(mode="json")
            assert action["state"] == "cancelled"
            assert action["cleanup_state"] == "not_required"
            assert action["feedback"] is None
            assert tx.get(task.result_ref)["action_state"] == "cancelled"
        # A caller replay must observe the original cancellation, without a new action.
        replay = await runtime.operate.execute(ctx, prepared[0])
        assert replay.model_dump(mode="json") == action
        assert submissions == queries == []
    finally:
        release.set()
        await host.stop()


async def repair_job(runtime, ledger, maintenance):
    item = await memory(runtime)
    cache = runtime.executor.cache
    key, field, _ = cache.keys(item.ref.scope, item.content_hash)
    original = cache.client.hget(key, field)
    assert original == item.content.encode()
    cache.client.hset(key, field, b"corrupt")
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
    return job, incident, (cache, key, field), original


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
        cache, key, field = path
        assert cache.client.hget(key, field) == original
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

    def interrupted(item, operation_id, ctx=None):
        calls.append(operation_id)
        repair(item, operation_id, ctx)
        raise OSError("lost repair confirmation")

    monkeypatch.setattr(runtime.executor, "repair", interrupted)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        cache, key, field = path
        assert cache.client.hget(key, field) == original
        assert calls == [job.job_id]
        with runtime.foundation.uow.transaction() as tx:
            current = tx.read("incidents", incident.incident_id)["record"]
            assert current["state"] == "resolved"
            assert current["verification"] == "passed"
            assert current["verification_refs"]
            assert ledger.tasks.load(tx, job.job_id)[1].state == "succeeded"
    finally:
        await host.stop()
