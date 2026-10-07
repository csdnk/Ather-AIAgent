"""Recovery must reuse committed domain chunks without republishing early."""

import asyncio
import importlib
import importlib.util
from uuid import uuid4

import pytest
from test_ingress import runtime as runtime

from aether_agent_memory.remember.contracts.models import RememberRequest, SourceInput, TextInput
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError, now
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.worker import WorkerHost


def routes(runtime):
    module = "aether_agent_memory.runtime.temporal.remember"
    assert importlib.util.find_spec(module), "Remember background stages are not registered"
    registry = StageRegistry()
    importlib.import_module(module).register_remember(registry, runtime.remember)
    return registry


@pytest.mark.parametrize(
    "kind", ["extract", "project", "cleanup", "compress", "distill", "revalidate", "summarize"]
)
def test_all_remember_kinds_have_stage_routes(runtime, kind):
    stages = routes(runtime).policies("remember." + kind)
    assert list(stages) == ["prepare", "generate", "publish", "commit"]
    assert stages["generate"].effect_mode == "read"
    assert stages["publish"].effect_mode == "uncertain"


async def job_for(runtime, kind):
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
            content=TextInput(kind="text", text="A durable fact.\n" * 5),
        ),
    )
    item = runtime.remember.get(ctx, receipt.memories[0].memory_id)
    ledger = ExecutionLedger(
        runtime.foundation.tasks,
        TemporalConfiguration(deployment_id="test", endpoint="127.0.0.1:7233"),
    )
    with runtime.foundation.uow.transaction() as tx:
        task_id = runtime.remember.enqueue(tx, ctx, item, "remember." + kind)
        task = runtime.foundation.tasks.load(tx, task_id)[1]
        job = ledger.bind_admitted(tx, task)
    return ledger, job, item


@pytest.mark.asyncio
async def test_checkpoint_reuse_after_activity_ack_loss(runtime, workflow_client, monkeypatch):
    registry = routes(runtime)
    ledger, job, item = await job_for(runtime, "compress")
    calls = []
    compress = runtime.remember.compressor.compress

    async def counted(ctx, text):
        calls.append(text)
        return await compress(ctx, text)

    monkeypatch.setattr(runtime.remember.compressor, "compress", counted)
    save_step = ledger.save_step
    interrupted = False

    def lose_ack(tx, step, *args, **kwargs):
        nonlocal interrupted
        if step.stage == "generate" and not interrupted:
            interrupted = True
            assert tx.rows("remember_compression_parts")
            assert tx.rows("remember_artifacts") == []
            raise RuntimeError("worker lost before Activity result commit")
        return save_step(tx, step, *args, **kwargs)

    monkeypatch.setattr(ledger, "save_step", lose_ack)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            artifact = tx.read("remember_artifacts", runtime.remember.refkey(item.ref))
            assert artifact and artifact["published"]
            assert tx.read("remember_model_calls", job.job_id) == 2
        extra_provider_calls = len(calls) - 1
        assert extra_provider_calls == 0
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_model_output_without_checkpoint_consumes_remaining_budget(
    runtime, workflow_client, monkeypatch
):
    from aether_agent_memory.runtime.foundation.postgres import (
        PostgresTransaction as PostgresTransaction,
    )

    runtime.remember.policy = runtime.remember.policy.model_copy(update={"max_model_calls": 2})
    registry = routes(runtime)
    ledger, job, _ = await job_for(runtime, "compress")
    write = PostgresTransaction.write

    def interrupt_checkpoint(tx, table, key, value):
        if table == "remember_compression_parts":
            raise RuntimeError("crash after model output, before checkpoint commit")
        return write(tx, table, key, value)

    monkeypatch.setattr(PostgresTransaction, "write", interrupt_checkpoint)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            assert tx.read("remember_model_calls", job.job_id) == 2
            assert tx.rows("remember_compression_parts") == []
            assert tx.rows("remember_artifacts") == []
            assert ledger.tasks.load(tx, job.job_id)[1].state != "succeeded"
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_changed_model_space_before_first_delivery_rejects_job(runtime, workflow_client):
    registry = routes(runtime)
    ledger, job, _ = await job_for(runtime, "project")
    runtime.remember.model_space = "changed-model-space"
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            assert tx.rows("remember_chunk_vectors") == []
            assert tx.rows("remember_projection_targets") == []
            assert tx.rows("remember_manifests") == []
    finally:
        await host.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("extract", "candidate_count", 1),
        ("project", "projection", "ready"),
        ("cleanup", "physical_erasure", False),
        ("revalidate", "requires_new_evidence", True),
        ("summarize", None, None),
        ("distill", "candidate_count", 0),
    ],
)
async def test_background_workflow_commits_domain_result(
    runtime, workflow_client, kind, field, value
):
    from aether_agent_memory.remember.contracts.models import ExtractionResult

    if kind == "distill":

        class Review:
            async def review_episodes(self, *args):
                return ExtractionResult(
                    candidates=(), model_id="test-review", policy_version="remember_v5"
                )

        runtime.remember.extraction = Review()
    registry = routes(runtime)
    ledger, job, _ = await job_for(runtime, kind)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            task = ledger.tasks.load(tx, job.job_id)[1]
            if kind == "summarize":
                # New Working records have no summary input. A stale/manual
                # summary job is cancelled rather than recreating the removed flow.
                assert task.state == "cancelled", task
                assert tx.rows("remember_working_summaries") == []
                return
            assert task.state == "succeeded", task
            assert tx.get(task.result_ref)[field] == value
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_projection_lost_ack_queries_original_target(runtime, workflow_client, monkeypatch):
    registry = routes(runtime)
    ledger, job, item = await job_for(runtime, "project")
    project = runtime.remember.projections.project
    writes = []

    async def lost_ack(ctx, request):
        writes.append(request)
        await project(ctx, request)
        raise OSError("lost projection response")

    monkeypatch.setattr(runtime.remember.projections, "project", lost_ack)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            assert (
                tx.read("remember_manifests", runtime.remember.refkey(item.ref))["state"] == "ready"
            )
            assert tx.read("remember_model_calls", job.job_id) == 1
        assert len(writes) == 1
        assert writes[0].operation_id == job.job_id
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_changed_policy_cannot_reuse_committed_chunk(runtime, workflow_client, monkeypatch):
    from contextvars import Context

    from aether_agent_memory.remember.basic.temporal_background import BackgroundStages

    registry = routes(runtime)
    ledger, job, _ = await job_for(runtime, "compress")
    generate = BackgroundStages.generate
    interrupted = False

    async def change_policy_after_generation(stages, step, **kwargs):
        nonlocal interrupted
        result = await generate(stages, step, **kwargs)
        if not interrupted:
            interrupted = True

            def mutate():
                with runtime.foundation.uow.transaction() as outside:
                    policy = outside.read("remember_task_policy", job.job_id)
                    outside.write(
                        "remember_task_policy",
                        job.job_id,
                        {**policy, "compression_require_ratio": True},
                    )

            # The test changes policy outside the Activity's guarded transaction.
            Context().run(mutate)
            raise OSError("lost completion")
        return result

    monkeypatch.setattr(BackgroundStages, "generate", change_policy_after_generation)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            assert tx.read("remember_model_calls", job.job_id) == 2
            assert tx.rows("remember_artifacts") == []
            assert ledger.tasks.load(tx, job.job_id)[1].state == "attention_required"
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_cleanup_of_absent_target_still_installs_tombstone(runtime, workflow_client):
    from aether_agent_memory.remember.basic.projection import projection_target

    registry = routes(runtime)
    ledger, job, item = await job_for(runtime, "cleanup")
    target = projection_target(item.ref, item.content_hash, runtime.remember.model_space)
    with runtime.foundation.uow.transaction() as tx:
        tx.write(
            "remember_projection_targets",
            target.vector_id,
            {"target": target.model_dump(mode="json"), "task_id": "old", "cleanup": "pending"},
        )
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/{job.kind}/{job.job_id}").result(), 60
        )
        with runtime.foundation.uow.transaction() as tx:
            intent = tx.read(runtime.vectors.projection_namespace, target.vector_id)
            assert intent["deleted"] is True
        assert (
            await runtime.projections.inspect(
                runtime.foundation.identity.context("alice"), target, "absent"
            )
        ).state == "absent"
        from aether_agent_memory.remember.contracts.models import ProjectionRequest

        ctx = runtime.foundation.identity.context("alice")
        with pytest.raises(FoundationError) as error:
            await runtime.projections.project(
                ctx,
                ProjectionRequest(
                    operation_id="late",
                    target=target,
                    vector=(1.0,) * runtime.vectors.dimensions,
                    deadline_at=ctx.deadline_at,
                ),
            )
        assert error.value.code == "IDEMPOTENCY_CONFLICT"
    finally:
        await host.stop()
