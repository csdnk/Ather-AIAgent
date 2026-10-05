"""Real unchanged P2 RPC integration. This is current-engine, not cloud backend evidence."""

import asyncio
import os
from hashlib import sha256
from uuid import uuid4

import pytest

from aether_agent_memory.p2.client import P2GrpcClient
from aether_agent_memory.p2.vectors import CurrentP2Vectors
from aether_agent_memory.recall.contracts.models import VectorSearchRequest
from aether_agent_memory.remember.basic.projection import projection_target
from aether_agent_memory.remember.contracts.models import MemoryRef, ProjectionRequest
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.temporal.ingress import InputStore
from azure_test_runtime import Foundation

pytestmark = pytest.mark.integration


@pytest.fixture
async def p2():
    endpoint = os.environ.get("P3_TEST_P2_ENDPOINT")
    if not endpoint:
        if os.environ.get("P3_REQUIRE_CURRENT_P2"):
            pytest.fail("current P2 endpoint is required for real integration")
        pytest.skip("set P3_TEST_P2_ENDPOINT to an unchanged running P2 gRPC service")
    namespace = "p3-test-" + uuid4().hex
    client = P2GrpcClient(endpoint, bucket=namespace, collection=namespace)
    await client.connect()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def identity(tmp_path):
    foundation = Foundation(tmp_path / "reference.db")
    scope = Scope(tenant_id="p3-test", application_id="app", user_id="alice", agent_id="agent")
    principal = Principal(
        principal_id="alice", auth_epoch=1, permissions=tuple(Permission), home_scope=scope
    )
    foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    ctx = foundation.identity.context("alice", timeout_seconds=60)
    yield foundation, ctx, scope
    foundation.close()


async def test_actual_object_put_read_range_metadata_and_delete(p2):
    data = "真实 P2 字节校验 abcdef".encode()
    key = "checks/object-" + uuid4().hex
    try:
        meta = await p2.put_object(key, data)
        assert meta.size == len(data) and meta.key == key and meta.bucket == p2.bucket
        result = await p2.get_object_result(key)
        assert result is not None and result.data == data
        assert result.meta is not None and result.meta.size == len(data)
        assert await p2.read_range(key, 2, 8) == data[2:8]
        assert await asyncio.to_thread(p2.get_object_sync, key) == data
        assert await p2.delete_object(key)
        assert await p2.get_object(key) is None
    finally:
        await p2.delete_object(key)


async def test_actual_input_can_be_loaded_by_another_worker_directory(p2, identity, tmp_path):
    foundation, ctx, _ = identity
    a = InputStore(foundation.uow, foundation.identity, tmp_path / "worker-a", objects=p2)
    b = InputStore(foundation.uow, foundation.identity, tmp_path / "worker-b", objects=p2)
    content = "跨 Worker 的真实输入".encode()
    ref = await asyncio.to_thread(a.persist, ctx, "input-" + uuid4().hex, content, "text/plain")
    try:
        assert await asyncio.to_thread(b.read, ctx, ref) == content
        assert not list((tmp_path / "worker-a").glob("*"))
        assert not list((tmp_path / "worker-b").glob("*"))
        with foundation.uow.transaction() as tx:
            record = tx.get(ref)
        assert record is not None and record["storage"] == "p2"
        assert await p2.get_object(record["object_key"]) == content
    finally:
        with foundation.uow.transaction() as tx:
            record = tx.get(ref)
        if record:
            await p2.delete_object(record["object_key"])


async def test_actual_projection_search_and_delete_consume_p2(p2, identity):
    foundation, ctx, scope = identity
    vectors = CurrentP2Vectors(foundation.uow, foundation.identity, "unit-cosine-v1", 2, p2)
    target = projection_target(
        MemoryRef(scope=scope, memory_id="m" + uuid4().hex, version=1),
        sha256(b"actual").hexdigest(),
        "unit-cosine-v1",
        generation="generation-1",
        body_hash=sha256(b"actual").hexdigest(),
    )
    try:
        result = await vectors.project(
            ctx,
            ProjectionRequest(
                operation_id="project-" + uuid4().hex,
                target=target,
                vector=(1, 0),
                deadline_at=ctx.deadline_at,
            ),
        )
        assert result.state == "verified" and result.payload_matches and result.searchable
        result = await vectors.search(
            ctx,
            VectorSearchRequest(
                selection={},
                vector=(1, 0),
                model_space="unit-cosine-v1",
                limit=5,
                deadline_at=ctx.deadline_at,
            ),
        )
        assert result.coverage == "complete"
        assert [candidate.target for candidate in result.candidates] == [target]
        gone = await vectors.delete(ctx, target, "delete-" + uuid4().hex)
        assert gone.state == "absent"
        assert await p2.search_vectors((1, 0)) == []
    finally:
        await p2.delete_vectors([target.vector_id])


async def test_actual_p2_temporal_save_and_runtime_restart(p2, temporal_server, tmp_path):
    from aether_agent_memory.remember.contracts.models import (
        RememberRequest,
        SourceInput,
        TextInput,
    )
    from aether_agent_memory.runtime.contracts.models import ScopeSelector
    from aether_agent_memory.runtime.foundation.common import now
    from aether_agent_memory.runtime.temporal.bridge import IntentBridge
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.gateway import TemporalGateway, connect_client
    from aether_agent_memory.runtime.temporal.ingress import CommandAdmission, register_ingress
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
    from aether_agent_memory.runtime.temporal.registry import StageRegistry
    from aether_agent_memory.runtime.temporal.worker import WorkerHost
    from azure_test_runtime import create_runtime

    scope = Scope(tenant_id="actual-p2", application_id="app", user_id="alice", agent_id="agent")
    principal = Principal(
        principal_id="alice", auth_epoch=1, permissions=tuple(Permission), home_scope=scope
    )
    runtime_options = {
        "p2": p2,
        "embedding_profile": "lexical",
        "vectors_factory": lambda uow, identity, space, dimensions: CurrentP2Vectors(
            uow,
            identity,
            space,
            dimensions,
            p2,
        ),
    }
    runtime = create_runtime(tmp_path / "reference.db", tmp_path / "cache", **runtime_options)
    runtime.foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    ctx = runtime.foundation.identity.context(
        "alice", operation_id="op" + uuid4().hex, timeout_seconds=90
    )
    temporal_config = TemporalConfiguration(
        deployment_id="p2-" + uuid4().hex, endpoint=temporal_server.endpoint
    )
    ledger = ExecutionLedger(runtime.foundation.tasks, temporal_config)
    inputs = InputStore(
        runtime.foundation.uow, runtime.foundation.identity, tmp_path / "inputs", objects=p2
    )
    registry = StageRegistry()
    register_ingress(registry, ledger, inputs, runtime.remember)
    admission = CommandAdmission(ledger, inputs)
    request = RememberRequest(
        selection=ScopeSelector(session_id="s1"),
        source=SourceInput(
            kind="conversation", external_id=uuid4().hex, external_version="1", occurred_at=now()
        ),
        content=TextInput(kind="text", text="Actual P2 durable business body."),
    )
    ref = await asyncio.to_thread(
        inputs.persist,
        ctx,
        ctx.operation_id,
        request.model_dump_json().encode(),
        "application/json",
    )
    job = admission.accept(ctx, "remember.save", ref)
    client = await connect_client(temporal_config)
    worker = WorkerHost(client, ledger, registry)
    await worker.start()
    try:
        await IntentBridge(ledger, TemporalGateway(client, ledger)).flush()
        await asyncio.wait_for(
            client.get_workflow_handle(
                f"p3/{temporal_config.deployment_id}/{job.kind}/{job.job_id}"
            ).result(),
            60,
        )
        result = admission.result(ctx, job.job_id)
        assert result and result["saved"]
        memory_id = result["memories"][0]["memory_id"]
        item = runtime.remember.get(ctx, memory_id)
        assert item.content == "Actual P2 durable business body."
        assert not list((tmp_path / "inputs").glob("*"))
        assert not list((tmp_path / "reference.db.bodies").glob("*"))
    finally:
        await worker.stop()
        runtime.close()
    restarted = create_runtime(
        tmp_path / "reference.db", tmp_path / "other-cache", **runtime_options
    )
    try:
        new_ctx = restarted.foundation.identity.context("alice", timeout_seconds=30)
        restored = restarted.remember.get(new_ctx, memory_id)
        assert restored.ref == item.ref and restored.content == item.content
        # Exact provider content, not only the reference DTO, must be present.
        location = restarted.remember.bodies.location(restored.ref.scope, restored.content)
        assert await p2.get_object(location.object_key) == restored.content.encode()
        resumed_ledger = ExecutionLedger(restarted.foundation.tasks, temporal_config)
        resumed_inputs = InputStore(
            restarted.foundation.uow,
            restarted.foundation.identity,
            tmp_path / "resumed-inputs",
            objects=p2,
        )
        assert (
            CommandAdmission(resumed_ledger, resumed_inputs).result(new_ctx, job.job_id) == result
        )
    finally:
        restarted.close()


async def test_actual_p2_process_restart_restores_objects_vectors_and_tombstones(p2):
    import subprocess
    import time

    from aether_agent_memory.p2.models import EmbeddingRecord

    container = os.environ.get("P3_TEST_P2_RESTART_CONTAINER")
    if not container:
        pytest.skip("explicit dedicated P2 container restart opt-in required")
    assert container == "aether-current-p2" and p2.endpoint == "127.0.0.1:35052", (
        "restart is restricted to the dedicated local integration container"
    )
    key = "restart/" + uuid4().hex
    content = b"P2 process restart must preserve exact bytes."
    records = [
        EmbeddingRecord(
            request_id="restart-test",
            trace_id="restart-test",
            source_id=key,
            object_id=key,
            chunk_id=kind + uuid4().hex,
            chunk_text="",
            vector=[1.0, 0.0],
            embedding_model="restart-test",
            metadata={"kind": kind},
        )
        for kind in ("live", "deleted")
    ]
    await p2.put_object(key, content)
    await p2.upsert_vectors(records)
    await p2.delete_vectors([records[1].chunk_id])
    await p2.close()
    completed = await asyncio.to_thread(
        subprocess.run, ["docker", "restart", container], capture_output=True, timeout=45
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                restored = await p2.get_object(key)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.1)
        assert restored == content
        hits = await p2.search_vectors((1, 0), top_k=10)
        assert [hit.id for hit in hits] == [records[0].chunk_id]
        assert hits[0].metadata["kind"] == "live"
        # Current P2's persisted tombstone must also hide a late InsertVector.
        await p2.upsert_vectors([records[1]])
        assert [hit.id for hit in await p2.search_vectors((1, 0), top_k=10)] == [
            records[0].chunk_id
        ]
    finally:
        await p2.delete_object(key)
        await p2.delete_vectors([record.chunk_id for record in records])
