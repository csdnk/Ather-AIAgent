"""Public progress and automatic repair against real SQLite and cache files."""

import asyncio
import copy
import json
import threading

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_continuous_service import configuration as configuration
from tests.integration.test_continuous_service import eventually, headers, ready_memory, save

from aether_agent_memory.operate.contracts.models import Tier
from aether_agent_memory.remember.basic.extraction import LiteralExtraction
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.flows.application import Service
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction


def test_cache_probe_preserves_crlf_and_rejects_invalid_utf8(configuration):
    service = Service(configuration)
    executor = service.runtime.executor
    memory = MemoryRef(
        memory_id="line_endings", version=1,
        scope=service.runtime.foundation.identity.context("alice").principal.home_scope,
    )
    data = b"line one\r\nline two\r\n"
    digest = text_hash(data.decode("utf-8"))
    try:
        with executor.db() as db:
            db.execute("INSERT INTO copies VALUES (?,?,?,?)",
                       (executor.key(memory), memory.model_dump_json(), Tier.COLD, digest))
        path = executor.path(memory, Tier.COLD)
        path.write_bytes(data)
        assert service.cache_maintenance.inspect(memory, digest)
        path.write_bytes(b"\xff")
        assert not service.cache_maintenance.inspect(memory, digest)
    finally:
        asyncio.run(service.close())


def test_runtime_counts_attention_without_scanning_completed_history(configuration, monkeypatch):
    service = Service(configuration)
    rf = service.runtime.foundation
    alice, eve = rf.identity.context("alice"), rf.identity.context("eve")
    try:
        pending = rf.sample.submit(alice, "visible", "private")
        hidden = rf.sample.submit(eve, "hidden", "private")
        baseline_deliveries = service.runtime.health.runtime(alice)["unacknowledged_deliveries"]
        with rf.uow.transaction() as tx:
            row = tx.read("tasks", pending.task_id)
            for index in range(200):
                history = copy.deepcopy(row)
                history["record"].update(task_id=f"history_{index}", state="succeeded")
                tx.write("tasks", f"history_{index}", history)
            attention = copy.deepcopy(row)
            attention["record"].update(task_id="needs_attention", state="attention_required")
            tx.write("tasks", "needs_attention", attention)
            for key, task in [("visible", row), ("hidden", tx.read("tasks", hidden.task_id))]:
                tx.write("outbox", key, {"event": {"subject": task["record"]["subject"]}})
                tx.write("deliveries", key, {"event_id": key, "state": "attention_required"})
            tx.write("deliveries", "done", {"event_id": "visible", "state": "acknowledged"})
            # Bound SQLite work, not wall-clock time: a namespace scan of the
            # completed records exceeds this budget even when Python filters it.
            connection = tx.raw.connection
            connection.set_progress_handler(lambda: 1, 500)
            try:
                tx.active_task_rows(include_attention=True)
                tx.pending_delivery_rows(include_attention=True)
            finally:
                connection.set_progress_handler(None, 0)
        original = SQLiteTransaction.rows

        def no_history_scan(tx, table):
            assert table not in {"tasks", "outbox", "deliveries"}
            return original(tx, table)

        monkeypatch.setattr(SQLiteTransaction, "rows", no_history_scan)
        result = service.runtime.health.runtime(alice)
        assert result["pending_tasks"] == 1
        assert result["attention_task_ids"] == ["needs_attention"]
        assert result["unacknowledged_deliveries"] == baseline_deliveries + 1
    finally:
        asyncio.run(service.close())


def prepared(service, client):
    assert save(client).is_success
    memory = MemoryRef.model_validate(eventually(lambda: ready_memory(client))["ref"])

    def locate():
        with service.runtime.executor.db() as db:
            row = db.execute(
                "SELECT tier FROM copies WHERE key=?", (service.runtime.executor.key(memory),)
            ).fetchone()
        return service.runtime.executor.path(memory, Tier(row[0])) if row else None

    return memory, eventually(locate)


@pytest.mark.parametrize("fault", ["corrupt", "missing"])
def test_automatic_cache_repair_and_independent_readback(configuration, fault):
    config = configuration.model_copy(
        update={"maintenance_principals": ("alice",), "operate_decay_seconds": 3600}
    )
    service = Service(config)
    with TestClient(service.app()) as client:
        memory, path = prepared(service, client)
        original = path.read_bytes()
        if fault == "corrupt":
            path.write_bytes(b"corrupt")
        else:
            path.unlink()

        def resolved():
            items = client.get("/p3/incidents", headers=headers()).json()
            return next((i for i in items if i["state"] == "resolved"), None)

        incident = eventually(resolved, seconds=14)
        assert path.read_bytes() == original
        assert incident["verification"] == "passed" and incident["verification_refs"]
        assert incident["subject"]["scope"] == memory.scope.model_dump(mode="json")
        assert client.get("/p3/incidents", headers=headers("eve")).json() == []
        with service.runtime.executor.db() as db:
            assert db.execute("SELECT count(*) FROM repairs").fetchone()[0] == 1


def test_repair_recovery_after_response_loss_does_not_resubmit(configuration):
    config = configuration.model_copy(
        update={"maintenance_principals": ("alice",), "operate_decay_seconds": 3600}
    )
    service = Service(config)
    original_repair = service.runtime.executor.repair
    calls = []

    def lose_response(item, operation_id):
        calls.append(operation_id)
        original_repair(item, operation_id)
        raise ConnectionError("response lost after durable repair")

    service.runtime.executor.repair = lose_response
    with TestClient(service.app()) as client:
        _, path = prepared(service, client)
        original = path.read_bytes()
        path.write_bytes(b"broken")
        eventually(
            lambda: any(
                i["state"] == "resolved"
                for i in client.get("/p3/incidents", headers=headers()).json()
            ),
            seconds=14,
        )
        assert path.read_bytes() == original and len(calls) == 1


def test_other_scope_and_revoked_identity_cannot_repair(configuration):
    config = configuration.model_copy(update={"operate_decay_seconds": 3600})
    service = Service(config)
    with TestClient(service.app()) as client:
        _, path = prepared(service, client)
        path.write_bytes(b"broken")
    service = Service(config)
    try:
        rf = service.runtime.foundation
        assert asyncio.run(service.cache_maintenance.sample(rf.identity.context("eve"))) == []
        ctx = rf.identity.context("alice")
        asyncio.run(rf.dispositions.cycle(ctx))
        task = rf.tasks.claim("manual", "maintenance", rf.tasks.clock())
        assert task is not None
        hydrate = service.cache_maintenance.remember.hydrate

        async def revoke(*args):
            await hydrate(*args)
            with rf.uow.transaction() as tx:
                raw = tx.read("identities", "alice")
                tx.write("identities", "alice", {**raw, "enabled": False})

        service.cache_maintenance.remember.hydrate = revoke
        with pytest.raises(Exception, match="revoked"):
            asyncio.run(service.cache_maintenance.run(ctx, task))
        assert path.read_bytes() == b"broken"
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
    pending = rf.sample.submit(ctx, "progress_rollback", "private text")
    task = rf.tasks.claim("test", "engineering", rf.tasks.clock())
    assert task.task_id == pending.task_id
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
