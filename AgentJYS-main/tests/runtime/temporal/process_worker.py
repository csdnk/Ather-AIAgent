"""Test-only rendezvous hooks; never imported by a production entry point."""

import argparse
import asyncio
import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path

import yaml

from aether_agent_memory.runtime.contracts.models import EffectStatus, Permission, Principal, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError, encode, fingerprint, now
from aether_agent_memory.runtime.temporal.activities import Activities, StageContext
from aether_agent_memory.runtime.temporal.models import StepResult
from azure_component_service import Service
from component_configuration import ComponentConfiguration as ServiceConfiguration


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), "utf-8")
    temporary.replace(path)


async def wait_until(check, seconds=60):
    async with asyncio.timeout(seconds):
        while not check():
            await asyncio.sleep(0.05)


def person():
    return Principal(
        principal_id="alice",
        auth_epoch=1,
        permissions=tuple(Permission),
        home_scope=Scope(tenant_id="test", application_id="p3", user_id="alice", agent_id="alice"),
    )


def evidence(app, job_id, result_ref=None):
    with app.uow.transaction() as tx:
        _, task = app.tasks.load(tx, job_id)
        bound = tx.read("temporal_bindings", job_id)
        binding = bound["binding"] or {}
        ref = task.result_ref or result_ref
        return {
            "job_id": job_id,
            "input_hash": task.input_hash,
            "state": task.state,
            "result_ref": ref.model_dump(mode="json"),
            "first_run_id": binding.get("first_run_id") or bound.get("observed_run_id"),
            "workflow_id": bound["workflow_id"],
            "attempt": task.attempt,
            "query_attempt": task.query_attempt,
            "business_effect_count": tx.revision(ref) or 0,
        }


async def rendezvous(args, value):
    write(args.directory / "rendezvous.json", {"scenario": args.scenario, **value})
    # Parent terminates this exact process. No sleep-based approximation of a commit.
    await asyncio.Event().wait()


async def domain(args):
    directory = args.directory
    identity = directory / "identity.yaml"
    if not identity.exists():
        identity.write_text(
            yaml.safe_dump(
                {
                    "revision": 1,
                    "tenants": [{"tenant_id": "test"}],
                    "identities": [
                        {
                            "credential_sha256": hashlib.sha256(b"alice").hexdigest(),
                            "principal": person().model_dump(mode="json"),
                        }
                    ],
                }
            ),
            "utf-8",
        )
    config = ServiceConfiguration(
        data_dir=directory / "state",
        identity_file=identity,
        embedding_profile="injected",
        temporal={"deployment_id": fingerprint(str(directory))[:32], "endpoint": args.endpoint},
        poll_seconds=0.02,
        periodic_seconds=2,
        shutdown_seconds=1,
        remember={"extraction_chunk_tokens": 16, "max_model_calls": 128},
    )
    service = Service(config)
    execution, runtime = service.execution, service.runtime
    app = runtime.foundation
    # Short test-only timeout makes the lost Activity response observable promptly.
    for stages in execution.registry.routes.values():
        for name, stage in list(stages.items()):
            stages[name] = replace(
                stage, policy=stage.policy.model_copy(update={"timeout_seconds": 5})
            )
    job_file = directory / "job.json"
    target = json.loads(job_file.read_text("utf-8")) if job_file.exists() else {}
    ctx = app.identity.context("alice", operation_id="process-save", timeout_seconds=180)
    original_execute = Activities._execute
    fired = False

    # Count invocations inside the real consumer's transaction; an Inbox replay
    # must not enter this wrapper a second time for the same event.
    for key, consumer in list(app.events.consumers.items()):

        def counted(tx, event, consumer=consumer, consumer_id=key[1]):
            receipt_key = fingerprint([consumer_id, event.event_id])
            tx.write(
                "process_consumers",
                receipt_key,
                (tx.read("process_consumers", receipt_key) or 0) + 1,
            )
            return consumer(tx, event)

        app.events.consumers[key] = counted

    async def after_step(activity_owner, step):
        nonlocal fired
        result = await original_execute(activity_owner, step)
        match = (
            args.scenario == "T05"
            and step.job.kind == "remember.save"
            and (step.stage == "admit_cache")
        )
        match |= (
            args.scenario == "T10"
            and step.job.kind == "remember.save"
            and (step.stage == "prepare")
        )
        match |= (
            args.scenario == "T09"
            and step.job.kind == "runtime.event_delivery"
            and (step.stage == "deliver")
        )
        if args.phase == "interrupt" and match and not fired:
            fired = True
            if args.scenario == "T09":
                target.update(job_id=step.job.job_id)
                write(job_file, target)
                value = evidence(app, step.job.job_id, result.result_ref)
                with app.uow.transaction() as tx:
                    value["inbox_signature"] = tx.read("inbox", step.job.job_id)["signature"]
            else:
                with app.uow.transaction() as tx:
                    task = app.tasks.load(tx, step.job.job_id)[1]
                value = evidence(
                    app,
                    step.job.job_id,
                    task.subject.model_copy(update={"object_type": "command_receipt"}),
                )
            await rendezvous(args, value)
        return result

    Activities._execute = after_step
    compress = runtime.remember.compressor.compress

    async def counted_compression(context, text):
        c = StageContext.current()
        with app.uow.transaction() as tx:
            checkpoints = tx.rows("remember_compression_parts")
        if args.phase == "interrupt" and args.scenario == "T04" and checkpoints:
            first_key, first_part = checkpoints[0]
            with app.uow.transaction() as tx:
                task = app.tasks.load(tx, c.task.task_id)[1]
            value = evidence(
                app,
                c.task.task_id,
                task.subject.model_copy(
                    update={"object_type": "processing_result", "object_id": c.task.task_id}
                ),
            )
            value.update(checkpoint_count=len(checkpoints), checkpoint_hash=fingerprint(first_part))
            target.update(job_id=c.task.task_id, first_checkpoint_key=first_key)
            write(job_file, target)
            await rendezvous(args, value)
        with app.uow.transaction() as tx:
            key = fingerprint(text)
            tx.write(
                "process_compression_calls",
                key,
                (tx.read("process_compression_calls", key) or 0) + 1,
            )
            if tx.read("process_first_chunk", "key") is None:
                tx.write("process_first_chunk", "key", key)
        return await compress(context, text)

    runtime.remember.compressor.compress = counted_compression
    try:
        if not target:
            payload = {
                "source": {
                    "kind": "conversation",
                    "external_id": "process-source",
                    "external_version": "1",
                    "occurred_at": now(),
                },
                "selection": {"session_id": "process"},
                "content": {
                    "kind": "text",
                    "text": "\n".join(f"Fact {i}: the durable value is {i}." for i in range(12)),
                },
            }
            ref = execution.inputs.persist(
                ctx, ctx.operation_id, encode(payload).encode(), "application/json"
            )
            job = execution.commands.accept(ctx, "remember.save", ref)
            target.update(job_id=job.job_id)
            write(job_file, target)
            if args.scenario == "T02":
                with app.uow.transaction() as tx:
                    task = app.tasks.load(tx, job.job_id)[1]
                await rendezvous(
                    args,
                    evidence(
                        app,
                        job.job_id,
                        task.subject.model_copy(update={"object_type": "command_receipt"}),
                    ),
                )
        await execution.start()
        await wait_until(lambda: execution.ready()["readiness"] == "ready")
        if args.scenario == "T04" and args.phase == "interrupt":
            receipt = await execution.await_result(ctx, target["job_id"], 40)
            item = runtime.remember.get(ctx, receipt["memories"][0]["memory_id"])
            with app.uow.transaction() as tx:
                target["job_id"] = runtime.remember.enqueue(tx, ctx, item, "remember.compress")
            write(job_file, target)
        if args.phase == "interrupt":
            await asyncio.Event().wait()
        job_id = target["job_id"]
        await execution.await_result(ctx, job_id, 45)
        with app.uow.transaction() as tx:
            binding = tx.read("temporal_bindings", job_id)
        await asyncio.wait_for(
            execution.client.get_workflow_handle(binding["workflow_id"]).result(), 60
        )
        value = evidence(app, job_id)
        if args.scenario in {"T02", "T05", "T10"}:
            receipt = execution.commands.result(ctx, job_id)
            assert receipt and len({m["memory_id"] for m in receipt["memories"]}) == len(
                receipt["memories"]
            )
        if args.scenario == "T04":
            with app.uow.transaction() as tx:
                part = tx.read("remember_compression_parts", target["first_checkpoint_key"])
                value["reused_checkpoint_hash"] = fingerprint(part)
                value["first_chunk_calls"] = tx.read(
                    "process_compression_calls", tx.read("process_first_chunk", "key")
                )
                artifacts = [
                    row for _, row in tx.rows("remember_artifacts") if row["task_id"] == job_id
                ]
                assert len(artifacts) == 1 and artifacts[0]["published"]
        if args.scenario == "T09":
            with app.uow.transaction() as tx:
                value.update(
                    inbox_signature=tx.read("inbox", job_id)["signature"],
                    delivery_state=tx.read("deliveries", job_id)["state"],
                    business_effect_count=tx.read("process_consumers", job_id),
                )
        write(directory / "result.json", value)
    finally:
        Activities._execute = original_execute
        await service.close()


async def stale(args):
    """A real SDK timeout leaves a provider thread alive; its fenced write loses."""
    from aether_agent_memory.runtime.temporal.bridge import IntentBridge
    from aether_agent_memory.runtime.temporal.config import (
        TemporalConfiguration,
        deployment_configuration,
    )
    from aether_agent_memory.runtime.temporal.gateway import TemporalGateway, connect_client
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
    from aether_agent_memory.runtime.temporal.locking import DirectoryLock
    from aether_agent_memory.runtime.temporal.registry import StageRegistry
    from aether_agent_memory.runtime.temporal.worker import WorkerHost
    from azure_test_runtime import Foundation

    lock = DirectoryLock()
    lock.acquire(args.directory / "state")
    app = Foundation(args.directory / "state/business.db", engineering_profile=True)
    app.identity.provision([(hashlib.sha256(b"alice").hexdigest(), person())], ())
    config = deployment_configuration(
        TemporalConfiguration(
            deployment_id=fingerprint(str(args.directory))[:32], endpoint=args.endpoint
        )
    )
    ledger = ExecutionLedger(app.tasks, config)
    registry = StageRegistry()
    release, finished = threading.Event(), threading.Event()
    calls = []

    async def execute(step):
        context = StageContext.current()
        calls.append("execute")
        if len(calls) == 1 and args.phase == "interrupt":

            def old_completion():
                assert release.wait(60)
                try:
                    with app.uow.transaction() as tx:
                        context.guard(tx)
                        tx.write("late_corruption", "value", True)
                except FoundationError as exc:
                    write(args.directory / "late.json", {"rejection": exc.code.value})
                finally:
                    finished.set()

            await context.blocking(old_completion)
        ref = context.task.subject.model_copy(update={"object_type": "engineering_result"})
        with app.uow.transaction() as tx:
            context.guard(tx)
            tx.put_if_revision(ref, {"winner": context.execution.epoch}, None)
        release.set()
        return StepResult(
            outcome="done",
            result_ref=ref,
            effect_status=EffectStatus.CONFIRMED,
            reason_code="SAVED",
        )

    async def reconcile(step):
        return StepResult(
            outcome="retry", effect_status=EffectStatus.NO_EFFECT, reason_code="VERIFIED_ABSENT"
        )

    registry.register(
        "engineering.save",
        "save",
        execute,
        reconcile,
        Permission.WRITE,
        "uncertain",
        timeout_seconds=0.3,
    )
    app.tasks.on_admitted = ledger.bind_admitted
    app.tasks.retry_seconds = 0.01
    ctx = app.identity.context("alice", timeout_seconds=180)
    task = app.sample.submit(ctx, "late", "stale Activity cannot replace the winner")
    client = await connect_client(config)
    host = WorkerHost(client, ledger, registry, shutdown_seconds=1)
    try:
        await host.start()
        await IntentBridge(ledger, TemporalGateway(client, ledger)).flush()
        with app.uow.transaction() as tx:
            workflow_id = tx.read("temporal_bindings", task.task_id)["workflow_id"]
        await asyncio.wait_for(client.get_workflow_handle(workflow_id).result(), 60)
        value = evidence(app, task.task_id)
        if args.phase == "interrupt":
            await wait_until(finished.is_set)
            await rendezvous(args, value)
        with app.uow.transaction() as tx:
            assert tx.read("late_corruption", "value") is None
        value["late_rejection"] = json.loads((args.directory / "late.json").read_text("utf-8"))[
            "rejection"
        ]
        write(args.directory / "result.json", value)
    finally:
        release.set()
        await host.stop()
        app.close()
        lock.release()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--phase", choices=["interrupt", "resume"], required=True)
    parser.add_argument("--azure-resources", type=Path, required=True)
    args = parser.parse_args()
    from azure_test_runtime import OwnedResources, _resources

    token = _resources.set(OwnedResources(json.loads(args.azure_resources.read_text("utf-8"))))
    try:
        await (stale(args) if args.scenario == "T07" else domain(args))
    finally:
        # Parent owns schema/prefix/collection cleanup across both child runs.
        _resources.reset(token)


if __name__ == "__main__":
    asyncio.run(main())
