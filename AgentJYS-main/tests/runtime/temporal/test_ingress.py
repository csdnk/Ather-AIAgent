import asyncio
import json
from hashlib import sha256
from uuid import uuid4

import pytest

from aether_agent_memory.remember.contracts.models import RememberRequest, SourceInput, TextInput
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Permission,
    Principal,
    Scope,
    ScopeSelector,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, now
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.ingress import (
    CommandAdmission,
    InputStore,
    register_ingress,
)
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.worker import WorkerHost
from azure_test_runtime import create_runtime


@pytest.fixture
def runtime(tmp_path, temporal_server):
    app = create_runtime(tmp_path / "p3.db", tmp_path / "cache", embedding_profile="injected")
    person = Principal(
        principal_id="alice",
        auth_epoch=1,
        permissions=tuple(Permission),
        home_scope=Scope(
            tenant_id="tenant", application_id="app", user_id="alice", agent_id="agent"
        ),
    )
    app.foundation.identity.provision([(sha256(b"alice").hexdigest(), person)])
    from temporal_test_support import seed_driver

    seed_driver(app, temporal_server)
    yield app
    app.close()


def setup(runtime):
    ledger = ExecutionLedger(
        runtime.foundation.tasks,
        TemporalConfiguration(deployment_id="test", endpoint="127.0.0.1:7233"),
    )
    inputs = InputStore(
        runtime.foundation.uow,
        runtime.foundation.identity,
        runtime.foundation.uow.path.parent / "inputs",
        max_bytes=runtime.remember.policy.max_input_bytes,
    )
    registry = StageRegistry()
    register_ingress(registry, ledger, inputs, runtime.remember)
    admission = CommandAdmission(ledger, inputs)
    return ledger, inputs, registry, admission


def test_upload_crash_before_admission_is_not_accepted(runtime, monkeypatch):
    _, inputs, _, _ = setup(runtime)
    ctx = runtime.foundation.identity.context("alice")

    def crash(*args):
        raise RuntimeError("crash after fsync, before input metadata commit")

    monkeypatch.setattr(inputs, "commit_input", crash)
    with pytest.raises(RuntimeError):
        inputs.persist(ctx, "operation", b"whole document", "text/plain")
    with runtime.foundation.uow.transaction() as tx:
        assert tx.rows("tasks") == []
        assert tx.rows("temporal_start_intents") == []
        assert tx.rows("records") == []


def test_reupload_different_bytes_conflicts(runtime):
    _, inputs, _, _ = setup(runtime)
    ctx = runtime.foundation.identity.context("alice")
    first = inputs.persist(ctx, "operation", b"first", "text/plain")
    assert inputs.persist(ctx, "operation", b"first", "text/plain") == first
    with pytest.raises(FoundationError) as error:
        inputs.persist(ctx, "operation", b"second", "text/plain")
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert inputs.read(ctx, first) == b"first"


def test_all_frontend_write_kinds_have_explicit_stages(runtime):
    _, _, registry, _ = setup(runtime)
    for kind in ("remember.save", "remember.document", "remember.correct"):
        assert list(registry.routes[kind]) == ["prepare", "persist", "commit", "admit_cache"]


@pytest.mark.asyncio
async def test_document_command_publishes_verified_original_bytes(runtime, workflow_client):
    ledger, inputs, registry, admission = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", operation_id=uuid4().hex, timeout_seconds=30)
    blob = inputs.persist(ctx, ctx.operation_id + "_bytes", b"source document", "text/plain")
    payload = {
        "document_id": "doc",
        "version": "1",
        "media_type": "text/plain",
        "blob_ref": blob.model_dump(mode="json"),
    }
    ref = inputs.persist(ctx, ctx.operation_id, json.dumps(payload).encode(), "application/json")
    job = admission.accept(ctx, "remember.document", ref)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/remember.document/{job.job_id}").result(),
            60,
        )
        result = admission.result(ctx, job.job_id)
        assert result["document_id"] == "doc"
        assert result["expected_hash"] == sha256(b"source document").hexdigest()
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_correction_command_commits_one_new_version(runtime, workflow_client):
    initial_ctx = runtime.foundation.identity.context("alice", timeout_seconds=30)
    source = SourceInput(
        kind="conversation", external_id=uuid4().hex, external_version="1", occurred_at=now()
    )
    receipt = await runtime.remember.save(
        initial_ctx,
        RememberRequest(
            selection=ScopeSelector(session_id="session"),
            source=source,
            content=TextInput(kind="text", text="old body"),
        ),
    )
    ledger, inputs, registry, admission = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", operation_id=uuid4().hex, timeout_seconds=30)
    payload = {
        "memory_id": receipt.memories[0].memory_id,
        "request": {
            "expected_version": 1,
            "content": "corrected body",
            "source": source.model_dump(mode="json"),
            "reason": "user correction",
        },
    }
    ref = inputs.persist(ctx, ctx.operation_id, json.dumps(payload).encode(), "application/json")
    job = admission.accept(ctx, "remember.correct", ref)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/remember.correct/{job.job_id}").result(),
            60,
        )
        assert admission.result(ctx, job.job_id)["memories"][0]["version"] == 2
        assert admission.accept(ctx, "remember.correct", ref) == job
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_saved_receipt_waits_for_business_commit(runtime, workflow_client, monkeypatch):
    ledger, inputs, registry, admission = setup(runtime)
    ctx = runtime.foundation.identity.context("alice", operation_id=uuid4().hex, timeout_seconds=60)
    request = RememberRequest(
        selection=ScopeSelector(session_id="session"),
        source=SourceInput(
            kind="conversation", external_id=uuid4().hex, external_version="1", occurred_at=now()
        ),
        content=TextInput(kind="text", text="durable input"),
    )
    ref = inputs.persist(
        ctx, ctx.operation_id, request.model_dump_json().encode(), "application/json"
    )
    job = admission.accept(ctx, "remember.save", ref)
    assert admission.result(ctx, job.job_id) is None
    entered, release = asyncio.Event(), asyncio.Event()
    persist = runtime.remember.bodies.persist

    async def paused(*args):
        entered.set()
        await release.wait()
        return await persist(*args)

    monkeypatch.setattr(runtime.remember.bodies, "persist", paused)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(entered.wait(), 60)
        assert admission.result(ctx, job.job_id) is None
        release.set()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/remember.save/{job.job_id}").result(), 60
        )
        assert admission.result(ctx, job.job_id)["saved"] is True
    finally:
        release.set()
        await host.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("effect_happened", [True, False])
async def test_p2_lost_ack_queries_original_body_without_rewrite(
    runtime, workflow_client, effect_happened
):
    ledger, inputs, registry, admission = setup(runtime)
    runtime.foundation.tasks.retry_seconds = 0.01
    writes, queries, objects = [], [], {}

    class P2:
        def get_object_sync(self, key):
            queries.append(key)
            return objects.get(key)

        async def get_object(self, key):
            return self.get_object_sync(key)

        async def put_object(self, key, value):
            writes.append(key)
            if effect_happened:
                objects[key] = value
            raise TimeoutError("provider reply lost")

    runtime.remember.bodies.p2 = P2()
    ctx = runtime.foundation.identity.context("alice", operation_id=uuid4().hex, timeout_seconds=30)
    request = RememberRequest(
        selection=ScopeSelector(session_id="session"),
        source=SourceInput(
            kind="conversation", external_id=uuid4().hex, external_version="1", occurred_at=now()
        ),
        content=TextInput(kind="text", text="original body"),
    )
    ref = inputs.persist(
        ctx, ctx.operation_id, request.model_dump_json().encode(), "application/json"
    )
    job = admission.accept(ctx, "remember.save", ref)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/remember.save/{job.job_id}").result(), 60
        )
        result = admission.result(ctx, job.job_id)
        assert bool(result and result["saved"]) == effect_happened
        assert len(writes) == 1
        assert len(queries) >= 2
        assert set(queries) == set(writes)
    finally:
        await host.stop()
