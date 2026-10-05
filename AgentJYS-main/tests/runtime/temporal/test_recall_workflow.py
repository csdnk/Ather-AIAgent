import asyncio
import importlib
import importlib.util
from hashlib import sha256

import pytest
from test_ingress import runtime as generation_runtime  # noqa: F401

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.runtime.contracts.models import ErrorCode, ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.worker import WorkerHost


@pytest.fixture(params=["basic", "generation"])
def runtime(request, tmp_path, temporal_server):
    from azure_test_runtime import create_runtime

    generated_runtime = request.getfixturevalue("generation_runtime")
    if request.param == "generation":
        yield generated_runtime
        return
    app = create_runtime(
        tmp_path / "basic.db", tmp_path / "basic-cache", embedding_profile="injected"
    )
    principal = generated_runtime.foundation.identity.context("alice").principal
    app.foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    from aether_agent_memory.recall.basic.service import Recall
    from temporal_test_support import seed_driver

    app.foundation.events.validators.pop("recall.access")
    app.recall = Recall(
        app.foundation.uow,
        app.foundation.identity,
        app.foundation.events,
        app.remember,
        app.embedding,
        app.vector_search,
        app.model_space,
        settings=app.recall_settings,
        tokenizer=app.recall.tokenizer,
    )
    seed_driver(app, temporal_server)
    yield app
    app.close()


def discovery_boundary(runtime):
    from aether_agent_memory.recall.basic.generation import GenerationRecall

    return (
        (runtime.recall.assembly.candidates, "search")
        if isinstance(runtime.recall, GenerationRecall)
        else (runtime.recall.sources, "discover")
    )


def setup(runtime):
    module = "aether_agent_memory.runtime.temporal.recall"
    assert importlib.util.find_spec(module), "Recall has no durable admission"
    implementation = importlib.import_module(module)
    ledger = ExecutionLedger(
        runtime.foundation.tasks,
        TemporalConfiguration(deployment_id="test", endpoint="127.0.0.1:7233"),
    )
    admission = implementation.RecallAdmission(ledger, runtime.recall)
    registry = StageRegistry()
    implementation.register_recall(registry, runtime.recall)
    return ledger, admission, registry


def request():
    return RecallRequest(query="durable recall", selection=ScopeSelector(), sources="long_term")


@pytest.mark.asyncio
async def test_pending_index_is_rediscovered_before_candidates_are_frozen(
    runtime, workflow_client, monkeypatch
):
    from aether_agent_memory.recall.basic.retrievers import SourceResult

    ledger, admission, registry = setup(runtime)
    boundary = runtime.recall.assembly if hasattr(runtime.recall, "assembly") else runtime.recall
    method = "discover" if hasattr(runtime.recall, "assembly") else "discover_candidates"
    discover = getattr(boundary, method)
    calls = []

    async def pending_once(*args):
        calls.append(1)
        result = await discover(*args)
        if len(calls) == 1:
            if isinstance(result, dict):
                return {**result, "reasons": ["long_term_index_pending"]}
            return [
                SourceResult(source="long_term", coverage="unavailable", reason="index_pending")
            ]
        return result

    monkeypatch.setattr(boundary, method, pending_once)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    job = admission.accept(ctx, request())
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        assert admission.read_result(ctx, job.job_id).outcome == "empty"
        assert len(calls) == 2
        with runtime.foundation.uow.transaction() as tx:
            assert ledger.tasks.load(tx, job.job_id)[1].attempt == 2
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_recall_survives_caller_disconnect(runtime, workflow_client, monkeypatch):
    ledger, admission, registry = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    job = admission.accept(ctx, request())
    entered, release = asyncio.Event(), asyncio.Event()
    boundary, method = discovery_boundary(runtime)
    discover = getattr(boundary, method)

    async def paused(*args):
        entered.set()
        await release.wait()
        return await discover(*args)

    monkeypatch.setattr(boundary, method, paused)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        caller = asyncio.create_task(admission.wait_result(ctx, job.job_id))
        await asyncio.wait_for(entered.wait(), 60)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert admission.accept(ctx, request()) == job
        release.set()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        pack = admission.read_result(ctx, job.job_id)
        assert pack.outcome == "empty"
        with runtime.foundation.uow.transaction() as tx:
            recall_ids = [key for key, _ in tx.rows("recall_requests")]
            if hasattr(runtime.recall, "assembly"):
                assert tx.read("recall_assembly", pack.recall_id) is not None
        assert recall_ids == [pack.recall_id]
        assert len(recall_ids) == 1
    finally:
        release.set()
        await host.stop()


@pytest.mark.asyncio
async def test_recall_resumes_before_original_deadline(runtime, workflow_client, monkeypatch):
    ledger, admission, registry = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    job = admission.accept(ctx, request())
    with runtime.foundation.uow.transaction() as tx:
        original = ledger.tasks.load(tx, job.job_id)[1]
    boundary, method = discovery_boundary(runtime)
    discover = getattr(boundary, method)
    deadlines = []

    async def interrupted(context, *args):
        deadlines.append(context.deadline_at)
        if len(deadlines) == 1:
            raise OSError("worker disconnected before discovery checkpoint")
        return await discover(context, *args)

    monkeypatch.setattr(boundary, method, interrupted)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            resumed = ledger.tasks.load(tx, job.job_id)[1]
        assert resumed.deadline_at == original.deadline_at
        assert set(deadlines) == {original.deadline_at}
        assert len(deadlines) == 2
        assert admission.read_result(ctx, job.job_id).outcome == "empty"
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_revoked_completed_pack_is_not_returned(runtime, workflow_client):
    ledger, admission, registry = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    job = admission.accept(ctx, request())
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        assert admission.read_result(ctx, job.job_id).outcome == "empty"
        with runtime.foundation.uow.transaction() as tx:
            identity = tx.read("identities", "alice")
            tx.write("identities", "alice", {**identity, "enabled": False})
        with pytest.raises(FoundationError) as error:
            admission.read_result(ctx, job.job_id)
        assert error.value.code == ErrorCode.FORBIDDEN
    finally:
        await host.stop()


async def seed(runtime):
    from aether_agent_memory.remember.contracts.models import (
        RememberRequest,
        SourceInput,
        TextInput,
    )
    from aether_agent_memory.runtime.foundation.common import now

    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    await runtime.remember.save(
        ctx,
        RememberRequest(
            selection=ScopeSelector(session_id="session"),
            source=SourceInput(
                kind="conversation", external_id="input", external_version="1", occurred_at=now()
            ),
            content=TextInput(kind="text", text="Deploy only after tests pass."),
        ),
    )
    # Fixture convergence is not the host's default synchronous wait contract.
    await runtime.drain(timeout_seconds=60)
    return RecallRequest(
        query="Deploy only after tests pass.",
        selection=ScopeSelector(session_id="session"),
        sources="both",
    )


@pytest.mark.asyncio
async def test_candidate_ack_loss_reuses_snapshot_and_rechecks_deleted_result(
    runtime, workflow_client, monkeypatch
):
    from aether_agent_memory.remember.contracts.models import DeleteRequest

    query = await seed(runtime)
    ledger, admission, registry = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    job = admission.accept(ctx, query)
    boundary, method = discovery_boundary(runtime)
    discover = getattr(boundary, method)
    calls = []

    async def counted(*args):
        calls.append(True)
        return await discover(*args)

    monkeypatch.setattr(boundary, method, counted)
    save_step = ledger.save_step
    interrupted = False

    def lose_ack(tx, step, *args, **kwargs):
        nonlocal interrupted
        if step.stage == "candidates" and not interrupted:
            interrupted = True
            raise OSError("worker lost before candidate Activity acknowledgement")
        return save_step(tx, step, *args, **kwargs)

    monkeypatch.setattr(ledger, "save_step", lose_ack)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        pack = admission.read_result(ctx, job.job_id)
        assert pack.outcome == "available" and pack.groups
        assert set(pack.selected_sources) == {"working", "long_term"}
        assert len(calls) == 2  # one per source, none on the recovered delivery
        memory = runtime.remember.get(ctx, pack.groups[0].items[0].memory.memory_id)
        runtime.remember.delete(
            runtime.foundation.identity.context("alice", timeout_seconds=60),
            memory.ref.memory_id,
            DeleteRequest(expected_revision=memory.object_revision, reason="delete"),
        )
        with pytest.raises(FoundationError) as error:
            admission.read_result(ctx, job.job_id)
        assert error.value.code == ErrorCode.RESULT_INVALIDATED
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_plan_changed_before_commit_is_not_published(runtime, workflow_client, monkeypatch):
    from aether_agent_memory.recall.basic.generation import GenerationRecall
    from aether_agent_memory.recall.basic.temporal_stages import GenerationStages, RecallStages

    query = await seed(runtime)
    owner = GenerationStages if isinstance(runtime.recall, GenerationRecall) else RecallStages
    assemble = owner.assemble

    async def corrupt(stages, step):
        result = await assemble(stages, step)
        with runtime.foundation.uow.transaction() as tx:
            ref = stages.ref("assembled")
            current = tx.get(ref)
            current["data"]["tampered"] = True
            # Leave the signed stage hash unchanged, simulating damaged local state.
            tx.put_if_revision(ref, current, tx.revision(ref))
        return result

    monkeypatch.setattr(owner, "assemble", corrupt)
    ledger, admission, registry = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=60)
    job = admission.accept(ctx, query)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            assert tx.read("recall_requests", job.job_id)["pack"] is None
            assert not any(
                row["event"]["event_type"] == "recall.access"
                and row["event"]["payload"]["stage"] == "packed"
                for _, row in tx.rows("outbox")
            )
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_completed_task_cannot_be_redirected_to_another_pack(runtime, workflow_client):
    ledger, admission, registry = setup(runtime)
    contexts = [runtime.foundation.identity.context("alice", timeout_seconds=60) for _ in range(2)]
    jobs = [admission.accept(ctx, request()) for ctx in contexts]
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        for job in jobs:
            await asyncio.wait_for(
                workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
            )
        with runtime.foundation.uow.transaction() as tx:
            first = ledger.tasks.load(tx, jobs[0].job_id)[1]
            value = tx.get(first.input_ref)
            tx.put_if_revision(
                first.input_ref,
                {**value, "recall_id": jobs[1].job_id},
                tx.revision(first.input_ref),
            )
        with pytest.raises(FoundationError) as error:
            admission.read_result(contexts[0], jobs[0].job_id)
        assert error.value.code == ErrorCode.VERSION_CONFLICT
    finally:
        await host.stop()
