"""Public progress and automatic repair against real Azure PostgreSQL and Redis."""

import asyncio
import copy
import json
import os
import threading
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_continuous_service import configuration as configuration
from tests.integration.test_continuous_service import eventually, headers, ready_memory, save

from aether_agent_memory.operate.contracts.models import Tier
from aether_agent_memory.remember.basic.extraction import LiteralExtraction
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from aether_agent_memory.runtime.foundation.requests import text_hash
from azure_component_service import Service


def convergence(service, check, *, seconds=14):
    """Retain the contract budget and capture evidence before resource teardown."""
    try:
        return eventually(check, seconds=seconds)
    except AssertionError:
        directory = os.environ.get("P3_DIAGNOSTIC_ROOT")
        if directory:
            rf = service.runtime.foundation
            with rf.uow.transaction() as tx:
                tasks = [
                    dict(task_id=key, record=row["record"], progress=row.get("progress"))
                    for key, row in tx.rows("tasks")
                ]
                records = {
                    table: tx.rows(table)
                    for table in (
                        "incidents",
                        "signal_samples",
                        "temporal_cache_repairs",
                        "temporal_bindings",
                        "temporal_diagnostics",
                        "temporal_ticks",
                        "temporal_steps",
                    )
                }
            with rf.telemetry.reader() as connection:
                logs = [
                    json.loads(row[0])
                    for row in connection.execute(
                        "SELECT data FROM node_logs ORDER BY sequence DESC LIMIT 128"
                    ).fetchall()
                ]
            tag = os.environ.get("P3_DIAGNOSTIC_TAG", "repair")
            case = os.environ.get("PYTEST_CURRENT_TEST", "unknown").split(" (")[0]
            suffix = sha256(case.encode()).hexdigest()[:12]
            path = Path(directory) / (tag + "-repair-diagnostic-" + suffix + ".json")
            path.write_text(
                json.dumps(
                    dict(
                        test=case,
                        tasks=tasks,
                        records=records,
                        repairs=repair_rows(service),
                        node_logs=logs,
                        maintenance_principals=service.config.maintenance_principals,
                        supervisor=service.execution.state,
                        copies=[
                            dict(
                                key=copy.key,
                                ref=copy.memory.model_dump(mode="json"),
                                hash=copy.content_hash,
                                readable=service.runtime.executor.inspect(
                                    copy.memory, copy.content_hash
                                ),
                            )
                            for copy in service.runtime.executor.copies()
                        ],
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
        raise


@pytest.mark.parametrize("method", ["inspect", "read_cached"])
def test_cache_probe_preserves_crlf_and_rejects_invalid_utf8(configuration, method):
    service = Service(configuration)
    executor = service.runtime.executor
    memory = MemoryRef(
        memory_id="line_endings",
        version=1,
        scope=service.runtime.foundation.identity.context("alice").principal.home_scope,
    )
    data = b"line one\r\nline two\r\n"
    digest = text_hash(data.decode("utf-8"))
    try:
        assert executor.cache.put_sync(memory.scope, data.decode("utf-8"))
        with executor.uow.transaction() as tx:
            tx.write(
                executor.table("copies"),
                executor.key(memory),
                {"memory": memory.model_dump(mode="json"), "tier": Tier.HOT.value, "hash": digest},
            )
        key, field, _ = executor.cache.keys(memory.scope, digest)
        assert executor.cache.raw_sync(memory.scope, digest) == data
        if method == "inspect":
            assert service.cache_maintenance.inspect(memory, digest)
        else:
            assert executor.read_cached(memory.scope, digest) == data.decode("utf-8")
        executor.cache.client.hset(key, field, b"\xff")
        if method == "inspect":
            assert not service.cache_maintenance.inspect(memory, digest)
        else:
            with pytest.raises(FoundationError) as error:
                executor.read_cached(memory.scope, digest)
            assert error.value.code == ErrorCode.CONTRACT_VIOLATION
    finally:
        asyncio.run(service.close())


def admit_command(service, ctx):
    inputs = service.execution.inputs
    ref = inputs.persist(ctx, ctx.operation_id, b'{"fixture":"durable input"}', "application/json")
    return service.execution.commands.accept(ctx, "remember.save", ref)


def test_runtime_counts_attention_without_scanning_completed_history(configuration, monkeypatch):
    service = Service(configuration)
    rf = service.runtime.foundation
    alice, eve = rf.identity.context("alice"), rf.identity.context("eve")
    try:
        pending = admit_command(service, alice)
        hidden = admit_command(service, eve)
        baseline_deliveries = service.runtime.health.runtime(alice)["unacknowledged_deliveries"]
        with rf.uow.transaction() as tx:
            row = tx.read("tasks", pending.job_id)
            for index in range(200):
                history = copy.deepcopy(row)
                history["record"].update(task_id=f"history_{index}", state="succeeded")
                tx.write("tasks", f"history_{index}", history)
            attention = copy.deepcopy(row)
            attention["record"].update(task_id="needs_attention", state="attention_required")
            tx.write("tasks", "needs_attention", attention)
            for key, task in [("visible", row), ("hidden", tx.read("tasks", hidden.job_id))]:
                tx.write("outbox", key, {"event": {"subject": task["record"]["subject"]}})
                tx.write("deliveries", key, {"event_id": key, "state": "attention_required"})
            tx.write("deliveries", "done", {"event_id": "visible", "state": "acknowledged"})
            # Real PostgreSQL queue queries must exclude completed history in SQL.
            active = tx.active_task_rows(include_attention=True)
            deliveries = tx.pending_delivery_rows(include_attention=True)
            assert not any(key.startswith("history_") for key, _ in active)
            assert {key for key, _ in deliveries} >= {"visible", "hidden"}
            raw_type, raw_scan = type(tx.raw), type(tx.raw).scan

        def no_raw_history_scan(raw, namespace):
            assert namespace not in {"p3_rf_tasks", "p3_rf_outbox", "p3_rf_deliveries"}
            return raw_scan(raw, namespace)

        monkeypatch.setattr(raw_type, "scan", no_raw_history_scan)
        original = PostgresTransaction.rows

        def no_history_scan(tx, table):
            assert table not in {"tasks", "outbox", "deliveries"}
            return original(tx, table)

        monkeypatch.setattr(PostgresTransaction, "rows", no_history_scan)
        result = service.runtime.health.runtime(alice)
        assert result["pending_tasks"] == 1
        assert result["attention_task_ids"] == ["needs_attention"]
        assert result["unacknowledged_deliveries"] == baseline_deliveries + 1
    finally:
        asyncio.run(service.close())


def prepared(service, client):
    assert save(client).is_success
    memory = MemoryRef.model_validate(eventually(lambda: ready_memory(client))["ref"])

    executor = service.runtime.executor

    def locate():
        copy = next((item for item in executor.copies() if item.memory == memory), None)
        return copy.content_hash if copy and executor.inspect(memory, copy.content_hash) else None

    digest = eventually(locate)

    def settled():
        with service.runtime.foundation.uow.transaction() as tx:
            return not tx.active_task_rows() and not tx.pending_delivery_rows()

    # A ready projection can precede an event-driven executor.ensure. Wait for
    # that legitimate initial admission before damaging bytes. The configured
    # retry interval keeps normal tier reevaluation outside the fault window.
    eventually(settled)
    return memory, digest


def damage(service, memory, digest, value):
    cache = service.runtime.executor.cache
    key, field, expires = cache.keys(memory.scope, digest)
    if value is None:
        cache.client.hdel(key, field, expires)
    else:
        cache.client.hset(key, field, value)


def repair_rows(service):
    executor = service.runtime.executor
    with executor.uow.transaction() as tx:
        return tx.rows(executor.table("repairs"))


@pytest.mark.parametrize("fault", ["corrupt", "missing"])
def test_automatic_cache_repair_and_independent_readback(configuration, fault):
    config = configuration.model_copy(
        update={
            "maintenance_principals": ("alice",),
            "operate_decay_seconds": 3600,
            "operate_retry_seconds": 3600,
        }
    )
    service = Service(config)
    with TestClient(service.app()) as client:
        memory, digest = prepared(service, client)
        original = service.runtime.executor.cache.raw_sync(memory.scope, digest)
        assert original is not None
        copies = [
            item
            for item in service.runtime.executor.copies()
            if item.memory.scope == memory.scope and item.content_hash == digest
        ]
        assert len(copies) == 2  # Working and episodic share this exact digest.
        subjects = {item.key for item in copies}
        damage(service, memory, digest, b"corrupt" if fault == "corrupt" else None)

        def resolved():
            items = client.get("/p3/incidents", headers=headers()).json()
            matching = [i for i in items if i["subject"]["object_id"] in subjects]
            return (
                matching
                if matching
                and all(i["state"] == "resolved" for i in matching)
                and all(service.runtime.executor.inspect(item.memory, digest) for item in copies)
                else None
            )

        incidents = convergence(service, resolved, seconds=14)
        # One shared Redis digest can be repaired before the second reference is
        # sampled. Every actual incident still requires its own original receipt.
        assert 1 <= len(incidents) <= len(copies)
        incident = incidents[0]
        assert service.runtime.executor.cache.raw_sync(memory.scope, digest) == original
        assert incident["verification"] == "passed" and incident["verification_refs"]
        assert incident["subject"]["scope"] == memory.scope.model_dump(mode="json")
        assert client.get("/p3/incidents", headers=headers("eve")).json() == []
        repairs = repair_rows(service)
        copy_by_key = {item.key: item for item in copies}
        expected = {
            i["operation_id"]: copy_by_key[i["subject"]["object_id"]].memory.model_dump_json()
            for i in incidents
        }
        assert len(repairs) == len(expected) == len(incidents)
        assert {job: row["memory"] for job, row in repairs} == expected
        assert all(row["hash"] == digest for _, row in repairs)
        assert all(i["verification"] == "passed" and i["verification_refs"] for i in incidents)


def test_repair_recovery_after_response_loss_does_not_resubmit(configuration):
    config = configuration.model_copy(
        update={
            "maintenance_principals": ("alice",),
            "operate_decay_seconds": 3600,
            "operate_retry_seconds": 3600,
        }
    )
    service = Service(config)
    original_repair = service.runtime.executor.repair
    calls = []

    def lose_response(item, operation_id, ctx=None):
        calls.append(operation_id)
        original_repair(item, operation_id, ctx)
        raise ConnectionError("response lost after durable repair")

    service.runtime.executor.repair = lose_response
    with TestClient(service.app()) as client:
        memory, digest = prepared(service, client)
        original = service.runtime.executor.cache.raw_sync(memory.scope, digest)
        assert original is not None
        copies = [
            item
            for item in service.runtime.executor.copies()
            if item.memory.scope == memory.scope and item.content_hash == digest
        ]
        assert len(copies) == 2
        subjects = {item.key for item in copies}
        damage(service, memory, digest, b"broken")

        def recovered():
            matching = [
                i
                for i in client.get("/p3/incidents", headers=headers()).json()
                if i["subject"]["object_id"] in subjects
            ]
            return (
                matching
                if matching
                and all(i["state"] == "resolved" for i in matching)
                and all(service.runtime.executor.inspect(item.memory, digest) for item in copies)
                else None
            )

        incidents = convergence(service, recovered, seconds=14)
        assert service.runtime.executor.cache.raw_sync(memory.scope, digest) == original
        assert 1 <= len(incidents) <= len(copies)
        assert all(i["verification"] == "passed" and i["verification_refs"] for i in incidents)
        copy_by_key = {item.key: item for item in copies}
        expected = {
            i["operation_id"]: (
                copy_by_key[i["subject"]["object_id"]].memory.model_dump_json(),
                digest,
            )
            for i in incidents
        }
        assert len(calls) == len(set(calls)) == len(expected)
        assert set(calls) == set(expected)  # No resubmission after a lost response.
        assert {job: service.runtime.executor.repair_record(job) for job in calls} == expected


def test_other_scope_and_revoked_identity_cannot_repair(configuration):
    config = configuration.model_copy(
        update={"operate_decay_seconds": 3600, "operate_retry_seconds": 3600}
    )
    service = Service(config)
    with TestClient(service.app()) as client:
        memory, digest = prepared(service, client)
        damage(service, memory, digest, b"broken")
    service = Service(config)
    try:
        rf = service.runtime.foundation
        assert asyncio.run(service.cache_maintenance.sample(rf.identity.context("eve"))) == []
        ctx = rf.identity.context("alice")
        asyncio.run(rf.dispositions.cycle(ctx))
        with rf.uow.transaction() as tx:
            task_id = next(
                key
                for key, row in tx.rows("tasks")
                if row["record"]["kind"] == "operate_repair_cache"
            )
        hydrate = service.cache_maintenance.remember.hydrate

        async def revoke(*args):
            await hydrate(*args)
            with rf.uow.transaction() as tx:
                raw = tx.read("identities", "alice")
                tx.write("identities", "alice", {**raw, "enabled": False})

        service.cache_maintenance.remember.hydrate = revoke

        def denied():
            with rf.uow.transaction() as tx:
                return tx.read("tasks", task_id)["record"]["state"] in {
                    "failed",
                    "attention_required",
                    "cancelled",
                }

        with TestClient(service.app()):
            eventually(denied)
            assert repair_rows(service) == []
        assert service.runtime.executor.cache.raw_sync(memory.scope, digest) == b"broken"
    finally:
        asyncio.run(service.close())


def test_progress_reuses_persisted_parts_after_restart(configuration):
    blocked = threading.Event()

    class Extraction(LiteralExtraction):
        def __init__(self, block):
            self.block, self.calls = block, []

        async def extract(self, ctx, request):
            self.calls.append(request.text)
            if self.block and len(self.calls) == 2:
                blocked.set()
                await asyncio.Event().wait()
            return await super().extract(ctx, request)

    config = configuration.model_copy(
        update={
            "shutdown_seconds": 0.1,
            "remember": configuration.remember.model_copy(update={"extraction_chunk_tokens": 16}),
        }
    )
    first = Extraction(True)
    service = Service(config, extraction=first)
    service.runtime.foundation.tasks.lease_seconds = 0.3
    with TestClient(service.app()) as client:
        assert save(
            client,
            text=" ".join(f"Sentence {i} describes a distinct preference." for i in range(12)),
        ).is_success
        eventually(blocked.is_set)
        tasks = client.get("/p3/tasks", headers=headers()).json()["items"]
        task_id = next(t["task_id"] for t in tasks if t["kind"] == "remember.extract")
        url = f"/p3/tasks/{task_id}/progress"
        progress = client.get(url, headers=headers()).json()
        assert progress["stages"][0]["completed_parts"] == 1
        assert first.calls[0] not in json.dumps(progress)
        assert client.get(url, headers=headers("eve")).status_code == 403
    second = Extraction(False)
    with TestClient(Service(config, extraction=second).app()) as client:
        eventually(
            lambda: (
                client.get(f"/p3/tasks/{task_id}", headers=headers()).json()["state"] == "succeeded"
            ),
            seconds=15,
        )
        assert first.calls[0] not in second.calls
        progress = client.get(url, headers=headers()).json()
        assert progress["stages"][0]["completed_parts"] == len(second.calls) + 1
        assert progress["wait"] is None


def test_progress_part_rolls_back_with_business_result(configuration):
    service = Service(configuration)
    rf = service.runtime.foundation
    ctx = rf.identity.context("alice")
    from aether_agent_memory.runtime.temporal.models import ExecutionRef, StepRequest

    pending = admit_command(service, ctx)
    with rf.uow.transaction() as tx:
        service.execution.ledger.begin(
            tx,
            StepRequest(job=pending, stage="prepare", ordinal=0, mode="execute"),
            ExecutionRef(
                namespace="default",
                workflow_id=f"p3/{configuration.temporal.deployment_id}/remember.save/{pending.job_id}",
                run_id="transaction-unit",
                activity_id="1",
                delivery_attempt=1,
                epoch=0,
            ),
        )
        task = rf.tasks.load(tx, pending.job_id)[1]
    try:
        with pytest.raises(RuntimeError), rf.uow.transaction() as tx:
            tx.write("test_parts", "one", {"content": "private output"})
            rf.tasks.progress.part(
                tx, ctx, task, "extraction", "test_parts", "one", config_version="v1"
            )
            raise RuntimeError("rollback")
        assert rf.tasks.progress.read(ctx, task.task_id)["stages"] == []
        with rf.uow.transaction() as tx:
            assert tx.read("test_parts", "one") is None
            tx.write("test_parts", "one", {"content": "private output"})
            for _ in range(2):
                rf.tasks.progress.part(
                    tx, ctx, task, "extraction", "test_parts", "one", config_version="v1"
                )
        assert rf.tasks.progress.read(ctx, task.task_id)["stages"][0]["completed_parts"] == 1
    finally:
        asyncio.run(service.close())
