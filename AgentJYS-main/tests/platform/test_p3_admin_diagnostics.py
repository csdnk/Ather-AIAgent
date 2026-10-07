"""Admin reads use the real business guards and retain the authenticated operator."""

import importlib.util
from datetime import UTC
from types import SimpleNamespace

import pytest
from test_ruoyi_p3 import setup

from aether_agent_memory.runtime.contracts.models import Permission, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError, now


def test_admin_read_module_exists():
    assert importlib.util.find_spec("aether_agent_memory.runtime.flows.admin_diagnostics")


def operator(tmp_path, role="aether_platform_admin", grants=None):
    adapter, identity, state, token = setup(tmp_path)
    adapter.verifier.config["auto_provision"] = True
    adapter.roles[role] = (
        (Permission.DIAGNOSE, Permission.RECOVER, Permission.CONFIGURE)
        if role == "aether_platform_admin"
        else ()
        if role == "aether_tenant_admin"
        else (Permission.READ,)
    )
    state.update(
        role_codes=[role], permissions=grants or ["aether:content:read", "aether:ops:read"]
    )
    if role == "aether_platform_admin":
        state["user_id"] = token["user_id"] = 199
    principal = adapter.authenticate(identity, "operator-token")
    ctx = identity.context_for_principal(principal)
    adapter.bind_context(ctx, "operator-token")
    return identity, ctx, state


def runtime(identity, tmp_path):
    from aether_agent_memory.recall.basic.service import Recall
    from aether_agent_memory.remember.basic.content import Bodies
    from aether_agent_memory.remember.basic.pipeline import RememberPipeline
    from aether_agent_memory.remember.basic.policy import RememberPolicy

    registrations = SimpleNamespace(class_limits={}, register=lambda *a, **k: None)
    events = SimpleNamespace(register_type=lambda *a, **k: None)
    remember = RememberPipeline(
        identity.uow,
        identity,
        registrations,
        events,
        None,
        None,
        None,
        "model",
        bodies=Bodies(tmp_path / "bodies", RememberPolicy()),
        tokenizer=SimpleNamespace(identifier="test"),
    )
    recall = Recall(
        identity.uow,
        identity,
        events,
        remember,
        None,
        None,
        "model",
        tokenizer=SimpleNamespace(identifier="test"),
    )
    return SimpleNamespace(
        foundation=SimpleNamespace(identity=identity, uow=identity.uow),
        remember=remember,
        recall=recall,
        operate=SimpleNamespace(key=lambda ref: ref.memory_id),
    )


def seed(
    runtime,
    memory_id="m1",
    tenant="old-tenant",
    user="old-user",
    status="active",
    expires=None,
    text=None,
):
    from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot, SourceRef
    from aether_agent_memory.runtime.foundation.requests import text_hash

    # Use the deployment's business app/agent mapping, never an impersonated context.
    scope = Scope(tenant_id=tenant, user_id=user, application_id="p3", agent_id="p3-agent")
    text = text or "private memory body " + memory_id
    source = SourceRef(
        source_id="source_" + memory_id,
        source_version=1,
        content_hash=text_hash(text),
        locator="conversation",
    )
    item = MemorySnapshot(
        ref=MemoryRef(scope=scope, memory_id=memory_id, version=1),
        revision=1,
        object_revision=1,
        kind="working",
        status=status,
        content=text,
        content_hash=text_hash(text),
        sources=(source,),
        projection_state="pending",
        created_at="1999-01-01T00:00:00.000Z" if expires else now(),
        expires_at=expires,
    )
    with runtime.foundation.uow.transaction() as tx:
        runtime.remember.put(tx, item)
        location = runtime.remember.bodies.stage(scope, text)
        tx.write(
            "remember_sources",
            source.source_id,
            {
                "valid": True,
                "revision": 1,
                "document": None,
                "ref": source.model_dump(mode="json"),
                "scope": scope.model_dump(mode="json"),
                "original_location": location.model_dump(mode="json"),
            },
        )
        runtime.remember.source_access.register(tx, source, text)
    return item


@pytest.mark.asyncio
async def test_admin_reads_body_without_widening_generic_identity(tmp_path):
    from aether_agent_memory.remember.basic.service import memory_ref
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    item = seed(host)
    admin = AdminDiagnostics(host, None)
    data = await admin.memory(ctx, "m1", "old-tenant", "old-user")
    assert data["body"]["content"] == item.content
    assert data["sources"][0]["content"] == item.content
    assert ctx.principal.home_scope.tenant_id == "aether_platform_operations"
    with identity.uow.transaction() as tx:
        assert not identity.permits(tx, ctx, Permission.READ, memory_ref(item.ref))
        events = [r for _, r in tx.rows("admin_content_access")]
        assert events[-1]["operator_id"] == ctx.principal.principal_id
        assert item.content not in str(events)
        assert not tx.rows("tasks")


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [("foreign", "old-user"), ("old-tenant", "other-user")])
async def test_wrong_target_never_returns_content(tmp_path, target):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    seed(host)
    with pytest.raises(FoundationError) as error:
        await AdminDiagnostics(host, None).memory(ctx, "m1", *target)
    assert error.value.code == "FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,grants",
    [
        ("aether_user", ["aether:content:read"]),
        ("aether_platform_admin", ["aether:ops:read"]),
        ("aether_platform_admin", ["aether:content:read"]),
    ],
)
async def test_content_requires_admin_and_explicit_grant(tmp_path, role, grants):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path, role, grants)
    host = runtime(identity, tmp_path)
    seed(host)
    with pytest.raises(FoundationError) as error:
        await AdminDiagnostics(host, None).memory(ctx, "m1", "old-tenant", "old-user")
    assert error.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_revoked_permission_and_deleted_body_fail_closed(tmp_path):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    seed(host, status="deleted")
    with pytest.raises(FoundationError) as error:
        await AdminDiagnostics(host, None).memory(ctx, "m1", "old-tenant", "old-user")
    assert error.value.code == "MEMORY_GONE"
    state["permissions"] = ["aether:ops:read"]
    with pytest.raises(FoundationError) as error:
        await AdminDiagnostics(host, None).memories(ctx, "old-tenant", "old-user")
    assert error.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_expired_body_and_summary_are_excluded(tmp_path):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    item = seed(host, expires="2000-01-01T00:00:00.000Z")
    admin = AdminDiagnostics(host, None)
    data = await admin.memory(ctx, "m1", "old-tenant", "old-user")
    assert data["body"]["outcome"] == "excluded"
    assert item.content not in str(data)
    listing = await admin.memories(ctx, "old-tenant", "old-user")
    assert listing["items"][0]["summary"] is None


@pytest.mark.asyncio
async def test_catalog_cursor_bound_to_operator_and_target(tmp_path):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    seed(host, "m1")
    seed(host, "m2")
    seed(host, "m3", user="other-user")
    admin = AdminDiagnostics(host, None)
    first = await admin.memories(ctx, "old-tenant", "old-user", limit=1)
    assert len(first["items"]) == 1 and first["next_cursor"]
    assert first["items"][0]["summary"].startswith("private memory body")
    second = await admin.memories(
        ctx, "old-tenant", "old-user", limit=1, cursor=first["next_cursor"]
    )
    assert first["items"][0]["ref"] != second["items"][0]["ref"]
    with pytest.raises(FoundationError):
        await admin.memories(ctx, "old-tenant", "other-user", cursor=first["next_cursor"])


@pytest.mark.asyncio
async def test_tenant_admin_cross_tenant_and_global_diagnostics_denied(tmp_path):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path, "aether_tenant_admin")
    host = runtime(identity, tmp_path)
    admin = AdminDiagnostics(host, None)
    assert (await admin.memories(ctx, "old-tenant", "old-user"))["items"] == []
    with pytest.raises(FoundationError):
        await admin.memories(ctx, "foreign", "old-user")
    with pytest.raises(FoundationError):
        await admin.diagnostics(ctx)


def seed_recall(host, item, generation=False):
    from aether_agent_memory.recall.contracts.models import (
        ContextGroup,
        ContextItem,
        ContextPack,
        RecallRecord,
    )
    from aether_agent_memory.remember.contracts.foundation import ContextGuardRequest, GuardStamp
    from aether_agent_memory.runtime.foundation.common import later

    record = RecallRecord(
        recall_id="recall1",
        scope=item.ref.scope,
        state="completed",
        stage="finalize",
        revision=1,
        deadline_at=later(now(), 60),
        result_available=True,
    )
    pack = ContextPack(
        recall_id="recall1",
        scope=item.ref.scope,
        outcome="available",
        selected_sources=("working",),
        coverage={"working": "complete", "long_term": "not_requested"},
        groups=(
            ContextGroup(
                group_id="g1",
                items=(
                    ContextItem(
                        memory=item.ref,
                        content=item.content,
                        sources=item.sources,
                        representation="original",
                    ),
                ),
            ),
        ),
        rendered_context=item.content,
        token_budget=100,
        tokens_used=10,
        tokenizer_id="test",
        policy_version=host.recall.policy_version,
        degradation_reasons=(),
        committed_at=now(),
    )
    guard = GuardStamp(
        memory=item.ref,
        object_revision=1,
        relations_revision=1,
        authorization_epoch=9,
        body_hash=item.content_hash,
        checked_at=now(),
    )
    with host.foundation.uow.transaction() as tx:
        tx.write(
            "recall_requests",
            "recall1",
            {"record": record.model_dump(mode="json"), "pack": pack.model_dump(mode="json")},
        )
        if generation:
            tx.write(
                "recall_assembly",
                "recall1",
                {"expectations": ContextGuardRequest(expected=(guard,)).model_dump(mode="json")},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [False, True])
async def test_recall_revalidates_original_memory_lifecycle_and_operator_epoch(
    tmp_path, generation
):
    from aether_agent_memory.recall.basic.generation import GenerationRecall
    from aether_agent_memory.remember.basic.service import memory_ref
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    item = seed(host)
    if generation:
        host.recall = GenerationRecall(host.recall, None, host.remember, host.remember)
    seed_recall(host, item, generation)
    admin = AdminDiagnostics(host, None)
    first = await admin.recall(ctx, "recall1", "old-tenant", "old-user")
    assert first["result"]["rendered_context"] == item.content
    with identity.uow.transaction() as tx:
        raw = tx.get(memory_ref(item.ref, versioned=True))
        raw["status"] = "deleted"
        tx.put_if_revision(
            memory_ref(item.ref, versioned=True),
            raw,
            tx.revision(memory_ref(item.ref, versioned=True)),
        )
    result = await admin.recall(ctx, "recall1", "old-tenant", "old-user")
    assert result["result"] is None and result["result_status"] == "invalidated"
    assert item.content not in str(result)


@pytest.mark.asyncio
async def test_role_revoked_while_body_io_in_flight_is_rechecked(tmp_path):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    seed(host)
    original = host.remember.bodies.read

    async def read(*args, **kwargs):
        value = await original(*args, **kwargs)
        state["role_codes"] = []
        return value

    host.remember.bodies.read = read
    with pytest.raises(FoundationError) as error:
        await AdminDiagnostics(host, None).memory(ctx, "m1", "old-tenant", "old-user")
    assert error.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_queue_observation_keeps_missing_metrics_unknown(tmp_path):
    from temporalio.api.taskqueue.v1 import PollerInfo
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueResponse

    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)

    class Service:
        async def describe_task_queue(self, request, **kwargs):
            assert request.namespace == "isolated"
            assert request.task_queue.name == "deploy-only.remember"
            return DescribeTaskQueueResponse(pollers=[PollerInfo(identity="worker-1")])

    execution = SimpleNamespace(
        client=SimpleNamespace(workflow_service=Service()),
        ledger=SimpleNamespace(
            config=TemporalConfiguration(
                deployment_id="one",
                namespace="isolated",
                task_queue_prefix="deploy-only",
                endpoint="localhost:7233",
            )
        ),
        workers=SimpleNamespace(execution_classes={"remember"}),
    )
    result = await AdminDiagnostics(host, execution).queues()
    assert result["status"] == "available"
    assert len(result["items"]) == 2
    for queue in result["items"]:
        assert queue["pollers"][0]["identity"] == "worker-1"
        assert queue["backlog_count_hint"] is None
        assert queue["backlog_status"] == "not_collected"


@pytest.mark.asyncio
async def test_actual_workflow_description_is_bounded_and_has_steps(tmp_path):
    from datetime import datetime

    from temporalio.api.common.v1 import ActivityType
    from temporalio.api.workflow.v1 import PendingActivityInfo
    from temporalio.api.workflowservice.v1 import DescribeWorkflowExecutionResponse
    from temporalio.client import WorkflowExecutionStatus

    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
    from aether_agent_memory.runtime.temporal.models import WorkflowBinding

    description = SimpleNamespace(
        raw_info=SimpleNamespace(first_run_id="first"),
        run_id="run",
        status=WorkflowExecutionStatus.RUNNING,
        workflow_type="P3TaskWorkflow",
        task_queue="deploy.remember",
        start_time=datetime.now(UTC),
        execution_time=None,
        close_time=None,
        raw_description=DescribeWorkflowExecutionResponse(
            pending_activities=[
                PendingActivityInfo(
                    activity_id="a1",
                    activity_type=ActivityType(name="p3.execute"),
                    attempt=2,
                    maximum_attempts=3,
                )
            ]
        ),
    )

    class Handle:
        async def describe(self, **kwargs):
            return description

    client = SimpleNamespace(namespace="isolated", get_workflow_handle=lambda *a, **kw: Handle())
    ledger = SimpleNamespace(
        config=TemporalConfiguration(
            deployment_id="one",
            namespace="isolated",
            task_queue_prefix="deploy",
            endpoint="localhost:7233",
        )
    )
    gateway = TemporalGateway(client, ledger)
    assert hasattr(gateway, "describe_details"), (
        "admin monitoring needs actual timing and pending activity evidence"
    )
    result = await gateway.describe_details(
        WorkflowBinding(
            namespace="isolated",
            workflow_id="p3/one/remember.project/t1",
            first_run_id="first",
            current_run_id="run",
            input_hash="a" * 64,
            plan_version="1",
        )
    )
    assert result["state"] == "running" and result["start_time"]
    assert result["pending_activities"][0]["attempt"] == 2
    assert "heartbeat_details" not in str(result)


@pytest.mark.asyncio
async def test_task_monitor_scopes_deployment_and_keeps_unknown_workflow_explicit(
    tmp_path, monkeypatch
):
    from aether_agent_memory.remember.basic.service import memory_ref
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    item = seed(host)

    class Gateway:
        async def describe_details(self, binding, **kwargs):
            if binding.workflow_id.endswith("failed"):
                raise TimeoutError("do-not-expose-provider-secret")
            return {"state": "not_found", "run_id": binding.current_run_id}

    execution = SimpleNamespace(
        ledger=SimpleNamespace(
            config=TemporalConfiguration(
                deployment_id="one", namespace="isolated", endpoint="localhost:7233"
            )
        ),
        gateway=Gateway(),
        client=None,
        state={"worker": "running", "token": "private-runtime-token"},
    )
    with identity.uow.transaction() as tx:
        for index in range(12):
            task_id = f"task{index:02d}"
            workflow_id = "p3/one/remember.project/" + task_id
            tx.write(
                "tasks",
                task_id,
                {
                    "record": {
                        "task_id": task_id,
                        "subject": memory_ref(item.ref).model_dump(mode="json"),
                        "state": "recovery_wait",
                        "kind": "operate.evaluate",
                        "effect_status": "unknown",
                        "payload": "private-task-input",
                    },
                    "created_at": f"2026-01-{index + 1:02d}T00:00:00.000Z",
                    "context": {"trace_id": "a" * 32},
                },
            )
            tx.write(
                "temporal_bindings",
                task_id,
                {
                    "namespace": "isolated",
                    "job": {"deployment_id": "one"},
                    "workflow_id": workflow_id,
                    "binding": {
                        "namespace": "isolated",
                        "workflow_id": workflow_id,
                        "first_run_id": "first",
                        "current_run_id": "run",
                        "input_hash": "a" * 64,
                        "plan_version": "1",
                    },
                },
            )
        tx.write("tasks", "foreign", {"record": {"task_id": "foreign"}})
        tx.write(
            "temporal_bindings",
            "foreign",
            {"namespace": "isolated", "job": {"deployment_id": "other"}},
        )
        tx.write(
            "task_waits",
            "task11",
            {
                "dependency_id": "p2",
                "reason_code": "REMOTE_WAIT",
                "wait_started_at": now(),
                "next_check_at": now(),
                "resume_mode": "query_only",
                "effect_status": "unknown",
            },
        )
    admin = AdminDiagnostics(host, execution)
    from aether_agent_memory.runtime.foundation.transactions import StorageTransaction

    original_read = StorageTransaction.read
    binding_reads = []

    def counted_read(tx, table, key):
        if table == "temporal_bindings":
            binding_reads.append(key)
        return original_read(tx, table, key)

    with monkeypatch.context() as patch:
        patch.setattr(StorageTransaction, "read", counted_read)
        result = await admin.diagnostics(ctx)
    assert binding_reads == [], "global task summaries must not issue one query per task"
    assert len(result["tasks"]["items"]) == 12
    assert result["tasks"]["items"][0]["task_id"] == "task11"
    assert result["tasks"]["items"][0]["workflow"]["state"] == "not_found"
    assert result["tasks"]["items"][10]["workflow"]["status"] == "not_checked_on_list"
    assert result["queue_metrics"]["status"] == "not_collected"
    assert result["task_summary"]["total"] == 12
    assert result["task_summary"]["by_state"] == {"recovery_wait": 12}
    assert result["task_summary"]["by_kind"] == {"operate.evaluate": 12}
    assert result["task_summary"]["latest_task_at"] == "2026-01-12T00:00:00.000Z"
    assert result["task_summary"]["created_last_24h"] == 0
    filtered = await admin.diagnostics(ctx, state="running")
    assert filtered["tasks"]["items"] == []
    assert filtered["task_summary"]["total"] == 12
    assert "private-runtime-token" not in str(result)
    assert "private-task-input" not in str(result)
    with pytest.raises(FoundationError):
        await admin.task(ctx, "foreign")
    detail = await admin.task(ctx, "task11")
    assert detail["task"]["effect_status"] == "unknown"
    assert detail["workflow"]["state"] == "not_found"
    assert detail["progress"]["wait"]["dependency_id"] == "p2"
    assert detail["progress"]["wait"]["reason_code"] == "REMOTE_WAIT"
    assert detail["progress"]["wait"]["resume_mode"] == "query_only"
    with identity.uow.transaction() as tx:
        stored = tx.read("temporal_bindings", "task11")
        stored["binding"]["workflow_id"] += "failed"
        tx.write("temporal_bindings", "task11", stored)
    unavailable = await admin.task(ctx, "task11")
    assert unavailable["workflow"]["status"] == "unavailable"
    assert "do-not-expose-provider-secret" not in str(unavailable)


@pytest.mark.asyncio
async def test_source_scope_mismatch_blocks_admin_detail(tmp_path):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    item = seed(host)
    with identity.uow.transaction() as tx:
        source = tx.read("remember_sources", item.sources[0].source_id)
        source["scope"]["tenant_id"] = "foreign"
        tx.write("remember_sources", item.sources[0].source_id, source)
    with pytest.raises(FoundationError) as error:
        await AdminDiagnostics(host, None).memory(ctx, "m1", "old-tenant", "old-user")
    assert error.value.code == "FORBIDDEN"


def test_admin_http_routes_are_get_only_and_no_store(tmp_path):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from aether_agent_memory.runtime.flows.admin_diagnostics import attach

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    seed(host)
    app = FastAPI()
    app.state.trusted_dependency = Depends(lambda: ctx)
    attach(app, host, None)
    assert all(
        route.methods == {"GET"} for route in app.routes if route.path.startswith("/p3/admin")
    )
    with TestClient(app) as client:
        response = client.get(
            "/p3/admin/memories", params={"tenant_id": "old-tenant", "user_id": "old-user"}
        )
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["items"][0]["summary"].startswith("private memory body")
        assert client.post("/p3/admin/memories").status_code == 405


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["好", "😀", "好😀", "好" * 240])
@pytest.mark.parametrize("remote", [False, True])
async def test_short_unicode_preview_never_reports_false_empty(tmp_path, text, remote):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    if remote:
        from aether_agent_memory.remember.basic.content import Bodies

        class RangeStore:
            def __init__(self):
                self.data = {}
                self.bytes_read = 0

            def put_object_sync(self, key, value):
                self.data[key] = value

            def get_object_sync(self, key):
                return self.data.get(key)

            async def read_range(self, key, start, end):
                assert 0 <= start <= end <= len(self.data[key])
                self.bytes_read += end - start
                return self.data[key][start:end]

        store = RangeStore()
        host.remember.bodies = Bodies(tmp_path / "remote", host.remember.policy, p2=store)
    seed(host, text=text)
    result = await AdminDiagnostics(host, None).memories(ctx, "old-tenant", "old-user")
    assert result["items"][0]["summary"] == text[:240]
    if remote:
        assert store.bytes_read <= 240 * 4


@pytest.mark.asyncio
async def test_memory_filters_are_server_side_and_cursor_bound(tmp_path):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    seed(host, "a1", status="archived")
    seed(host, "a2", status="archived")
    seed(host, "b1")
    admin = AdminDiagnostics(host, None)
    page = await admin.memories(ctx, "old-tenant", "old-user", limit=1, status="archived")
    assert page["items"][0]["status"] == "archived"
    with pytest.raises(FoundationError):
        await admin.memories(
            ctx, "old-tenant", "old-user", cursor=page["next_cursor"], status="active"
        )
    assert (await admin.memories(ctx, "old-tenant", "old-user", kind="semantic"))["items"] == []


@pytest.mark.asyncio
async def test_catalog_authority_rpc_count_is_bounded_by_page_not_history(tmp_path):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    for index in range(30):
        seed(host, memory_id=f"m{index}")
    checks = 0
    revalidate = identity.ruoyi_revalidate

    def checked(*args):
        nonlocal checks
        checks += 1
        return revalidate(*args)

    identity.ruoyi_revalidate = checked
    result = await AdminDiagnostics(host, None).memories(ctx, "old-tenant", "old-user", limit=1)
    assert len(result["items"]) == 1
    assert checks < 15, "single-page reads must not call authority once per historical object"


@pytest.mark.asyncio
async def test_placement_retains_real_nested_action_and_view_fields(tmp_path):
    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, state = operator(tmp_path)
    host = runtime(identity, tmp_path)
    item = seed(host)
    with identity.uow.transaction() as tx:
        tx.write(
            "operate_views",
            "m1",
            {
                "memory": item.ref.model_dump(mode="json"),
                "successful_reads": 12,
                "storage_watermark": 3,
                "event": {"payload": "must-not-leak"},
            },
        )
        tx.write(
            "operate_actions",
            "a1",
            {
                "intent": {
                    "action_id": "a1",
                    "created_at": now(),
                    "decision": {
                        "memory": item.ref.model_dump(mode="json"),
                        "outcome": "promote",
                        "current_tier": "cold",
                        "target_tier": "warm",
                        "policy_version": "p1",
                    },
                },
                "state": "failed",
                "revision": 2,
                "cleanup_state": "not_required",
                "feedback": {
                    "state": "failed",
                    "observed_at": now(),
                    "provider_operation_id": "original-op",
                },
            },
        )
    result = await AdminDiagnostics(host, None).memory(ctx, "m1", "old-tenant", "old-user")
    placement = result["placement"]
    assert placement["input"]["successful_reads"] == 12
    assert placement["actions"][0]["action_id"] == "a1"
    assert placement["actions"][0]["decision"]["target_tier"] == "warm"
    assert placement["actions"][0]["feedback"]["provider_operation_id"] == "original-op"
    assert "must-not-leak" not in str(placement)
