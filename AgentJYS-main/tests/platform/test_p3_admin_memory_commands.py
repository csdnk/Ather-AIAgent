"""Admin execution preserves actor proof and never borrows a target user's identity."""

import importlib.util
from types import SimpleNamespace

import pytest
from test_p3_admin_diagnostics import operator, runtime, seed

from aether_agent_memory.runtime.contracts.models import Permission, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError

GRANTS = [
    "aether:ops:read",
    "aether:content:read",
    "aether:memories:execute",
    "aether:memory:create",
    "aether:memory:update",
    "aether:memory:delete",
    "aether:recall:execute",
]


def fixture(tmp_path, role="aether_platform_admin"):
    identity, ctx, state = operator(tmp_path, role, list(GRANTS))
    adapter = identity.ruoyi_revalidate.__self__
    remote = adapter.verifier.remote

    def directory(method, path, **kwargs):
        if path.endswith("/status") and not kwargs.get("data", {}).get("token_hash"):
            return {
                "user_id": "17",
                "tenant_id": "2",
                "user_enabled": True,
                "tenant_enabled": True,
                "role_codes": ["aether_user"],
                "permissions": [],
            }
        return remote(method, path, **kwargs)

    adapter.verifier.remote = directory
    return identity, ctx, state, runtime(identity, tmp_path)


def test_admin_command_module_exists():
    assert importlib.util.find_spec("aether_agent_memory.runtime.flows.admin_memory_commands")


def test_bound_context_preserves_original_actor_and_live_revocation(tmp_path):
    from aether_agent_memory.remember.basic.service import memory_ref
    from aether_agent_memory.runtime.foundation.admin_execution import bind_admin_context

    identity, ctx, state, host = fixture(tmp_path)
    item = seed(host)
    bound = bind_admin_context(identity, ctx, "old-tenant", "old-user", "update")
    assert bound.principal.principal_id == ctx.principal.principal_id
    assert bound.principal.auth_epoch == ctx.principal.auth_epoch
    assert bound.principal.home_scope.user_id == "old-user"
    persisted = TrustedContext.model_validate(bound.model_dump(mode="json"))
    with identity.uow.transaction() as tx:
        identity.authorize(tx, persisted, Permission.CORRECT, memory_ref(item.ref))
        assert not identity.permits(tx, ctx, Permission.CORRECT, memory_ref(item.ref))
        assert not identity.permits(tx, persisted, Permission.DELETE, memory_ref(item.ref))
        assert tx.read("identities", ctx.principal.principal_id)[
            "principal"
        ] == ctx.principal.model_dump(mode="json")
    state["permissions"].remove("aether:memory:update")
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, persisted)


@pytest.mark.parametrize("field,value", [("user_id", "intruder"), ("tenant_id", "foreign")])
def test_forged_scope_cannot_reuse_durable_binding(tmp_path, field, value):
    from aether_agent_memory.runtime.foundation.admin_execution import bind_admin_context

    identity, ctx, _, _ = fixture(tmp_path)
    bound = bind_admin_context(identity, ctx, "old-tenant", "old-user", "create")
    forged = bound.model_copy(
        update={
            "principal": bound.principal.model_copy(
                update={"home_scope": bound.principal.home_scope.model_copy(update={field: value})}
            )
        }
    )
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, forged)


def test_delegation_cannot_borrow_actors_ambient_grant_to_another_user(tmp_path):
    from aether_agent_memory.remember.basic.service import memory_ref
    from aether_agent_memory.runtime.contracts.models import AuthorizationGrant
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )
    from aether_agent_memory.runtime.foundation.admin_execution import bind_admin_context

    identity, ctx, _, host = fixture(tmp_path)
    foreign = seed(host, memory_id="foreign", user="other-user")
    bound = bind_admin_context(identity, ctx, "old-tenant", "old-user", "update")
    grant = AuthorizationGrant(
        grant_id="ambient",
        grantee_id=ctx.principal.principal_id,
        grantee_tenant_id="old-tenant",
        resource=memory_ref(foreign.ref),
        permissions=(Permission.READ, Permission.WRITE, Permission.CORRECT),
        revision=1,
    )
    with identity.uow.transaction() as tx:
        tx.write("settings", "grants", [grant.model_dump(mode="json")])
        assert not identity.permits(tx, bound, Permission.CORRECT, memory_ref(foreign.ref))
    with pytest.raises(FoundationError):
        AdminMemoryCommands(host, None).prepare(
            ctx,
            AdminMemoryCommand(
                action="update",
                tenant_id="old-tenant",
                user_id="old-user",
                memory_id="foreign",
                expected_version=1,
                expected_object_revision=1,
                text="cannot change another user",
            ),
        )


def test_tenant_admin_and_unmapped_target_cannot_expand_scope(tmp_path):
    from aether_agent_memory.runtime.foundation.admin_execution import bind_admin_context

    identity, ctx, _, _ = fixture(tmp_path, "aether_tenant_admin")
    for tenant, user in [("foreign", "old-user"), ("old-tenant", "invented")]:
        with pytest.raises(FoundationError):
            bind_admin_context(identity, ctx, tenant, user, "create")


def test_target_requires_live_matching_native_membership_without_prior_login(tmp_path):
    from aether_agent_memory.runtime.foundation.admin_execution import bind_admin_context

    identity, ctx, _, _ = fixture(tmp_path)
    with identity.uow.transaction() as tx:
        assert all(
            row["principal"]["home_scope"]["user_id"] != "old-user"
            for _, row in tx.rows("identities")
        )
    bound = bind_admin_context(identity, ctx, "old-tenant", "old-user", "create")
    adapter = identity.ruoyi_revalidate.__self__
    remote = adapter.verifier.remote

    def changed(method, path, **kwargs):
        result = remote(method, path, **kwargs)
        return (
            {**result, "tenant_id": "3"} if not kwargs.get("data", {}).get("token_hash") else result
        )

    adapter.verifier.remote = changed
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, bound)


def test_command_model_rejects_scope_injection_and_stale_delete_contract():
    from pydantic import ValidationError

    from aether_agent_memory.runtime.flows.admin_memory_commands import AdminMemoryCommand

    base = {"action": "create", "tenant_id": "old-tenant", "user_id": "old-user", "text": "hello"}
    with pytest.raises(ValidationError):
        AdminMemoryCommand.model_validate({**base, "principal_id": "victim"})
    with pytest.raises(ValidationError):
        AdminMemoryCommand.model_validate(
            {
                "action": "delete",
                "tenant_id": "old-tenant",
                "user_id": "old-user",
                "memory_id": "m1",
                "expected_version": 1,
            }
        )


@pytest.mark.parametrize("action", ["update", "archive", "activate"])
def test_every_mutation_requires_captured_object_revision(tmp_path, action):
    from pydantic import ValidationError

    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )

    with pytest.raises(ValidationError):
        AdminMemoryCommand(
            action=action,
            tenant_id="old-tenant",
            user_id="old-user",
            memory_id="m1",
            expected_version=1,
            text="corrected",
        )
    _, ctx, _, host = fixture(tmp_path)
    seed(host)
    with pytest.raises(FoundationError) as exc:
        AdminMemoryCommands(host, None).prepare(
            ctx,
            AdminMemoryCommand(
                action=action,
                tenant_id="old-tenant",
                user_id="old-user",
                memory_id="m1",
                expected_version=1,
                expected_object_revision=2,
                text="corrected",
            ),
        )
    assert str(exc.value.code) == "VERSION_CONFLICT"


def test_uncertain_temporal_effect_is_pending_and_never_retryable(tmp_path):
    from aether_agent_memory.runtime.contracts.models import ErrorCode
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )

    _, ctx, _, host = fixture(tmp_path)

    class Uncertain:
        def lookup_operation(self, *args):
            return SimpleNamespace(state="found", job_id="unknown-job")

        def result(self, *args):
            raise FoundationError(ErrorCode.EXECUTION_INTERRUPTED, "unknown effect")

        def status(self, *args):
            return SimpleNamespace(
                state="attention_required",
                effect_status="unknown",
                error_code=ErrorCode.EXECUTION_INTERRUPTED,
            )

    admin = AdminMemoryCommands(host, Uncertain())
    admin.prepare(
        ctx,
        AdminMemoryCommand(
            action="create", tenant_id="old-tenant", user_id="old-user", text="remember"
        ),
    )
    result = admin.read(ctx, ctx.operation_id, "old-tenant", "old-user")
    assert result["status"] == "pending"
    assert result["retry_allowed"] is False


def test_admin_prepare_is_durable_actor_bound_and_rejects_changed_replay(tmp_path):
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )

    identity, ctx, _, host = fixture(tmp_path)
    command = AdminMemoryCommand(
        action="create", tenant_id="old-tenant", user_id="old-user", text="remember this"
    )
    admin = AdminMemoryCommands(host, None)
    first = admin.prepare(ctx, command)
    assert first["status"] == "pending"
    restored = AdminMemoryCommands(host, None)
    assert restored.prepare(ctx, command)["context"] == first["context"]
    with pytest.raises(FoundationError) as error:
        restored.prepare(ctx, command.model_copy(update={"text": "different"}))
    assert str(error.value.code) == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(FoundationError):
        restored.load(ctx, ctx.operation_id, "foreign", "old-user")
    assert first["context"]["principal"]["principal_id"] == ctx.principal.principal_id


def test_admin_delete_checks_exact_loaded_version_before_pipeline(tmp_path):
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )

    _, ctx, _, host = fixture(tmp_path)
    item = seed(host)
    admin = AdminMemoryCommands(host, None)
    with pytest.raises(FoundationError) as error:
        admin.prepare(
            ctx,
            AdminMemoryCommand(
                action="delete",
                tenant_id="old-tenant",
                user_id="old-user",
                memory_id=item.ref.memory_id,
                expected_version=2,
                expected_object_revision=1,
            ),
        )
    assert str(error.value.code) == "VERSION_CONFLICT"


def test_admin_unknown_admission_recovers_original_job_without_resubmit(tmp_path):
    from aether_agent_memory.runtime.contracts.models import ErrorCode
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )

    identity, ctx, _, host = fixture(tmp_path)

    class Admission:
        def accept(self, bound, kind, payload):
            # Simulated lost transport response *after* the durable ingress record.
            with identity.uow.transaction() as tx:
                tx.write(
                    "test_external_admission",
                    bound.operation_id,
                    {
                        "actor": bound.principal.principal_id,
                        "tenant": bound.principal.home_scope.tenant_id,
                        "user": bound.principal.home_scope.user_id,
                        "kind": kind,
                        "payload": payload,
                    },
                )
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "secret provider failure")

        def lookup_operation(self, bound, operation_id, kind):
            with identity.uow.transaction() as tx:
                row = tx.read("test_external_admission", operation_id)
            return SimpleNamespace(state="found" if row else "unconfirmed", job_id="original-job")

        def result(self, bound, job_id):
            return {"saved": True, "memories": [], "phase": "saved", "task_ids": []}

    admin = AdminMemoryCommands(host, Admission())
    row = admin.prepare(
        ctx,
        AdminMemoryCommand(
            action="create", tenant_id="old-tenant", user_id="old-user", text="remember"
        ),
    )
    admin.dispatch(row)
    result = AdminMemoryCommands(host, admin.execution).read(
        ctx, ctx.operation_id, "old-tenant", "old-user"
    )
    assert result["status"] == "succeeded"
    assert result["job_id"] == "original-job"
    assert result["result"]["saved"] is True
    assert "secret provider failure" not in str(result)
    with identity.uow.transaction() as tx:
        records = tx.rows("test_external_admission")
    assert len(records) == 1
    assert records[0][1]["actor"] == ctx.principal.principal_id
    assert records[0][1]["user"] == "old-user"


def test_scoped_event_context_keeps_delegation_and_revocation(tmp_path):
    from aether_agent_memory.runtime.foundation.admin_execution import bind_admin_context
    from aether_agent_memory.runtime.foundation.common import now
    from aether_agent_memory.runtime.foundation.requests import event_context

    identity, ctx, state, _ = fixture(tmp_path)
    bound = bind_admin_context(identity, ctx, "old-tenant", "old-user", "create")
    event = SimpleNamespace(
        initiator_id=ctx.principal.principal_id,
        initiator_auth_epoch=ctx.principal.auth_epoch,
        event_id="event1",
        request_id=bound.request_id,
        trace_id=bound.trace_id,
    )
    with identity.uow.transaction() as tx:
        resumed = event_context(tx, event, now())
        assert resumed.principal == bound.principal
        identity.revalidate(tx, resumed)
    state["permissions"].remove("aether:memory:create")
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, resumed)


def test_failed_temporal_job_is_a_failed_command_not_permanent_pending(tmp_path):
    from aether_agent_memory.runtime.contracts.models import ErrorCode
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )

    _, ctx, _, host = fixture(tmp_path)

    class FailedExecution:
        def lookup_operation(self, *args):
            return SimpleNamespace(state="found", job_id="failed-job")

        def result(self, *args):
            raise FoundationError(ErrorCode.EXECUTION_INTERRUPTED, "unsafe failure detail")

        def status(self, *args):
            return SimpleNamespace(state="failed", error_code=ErrorCode.EXECUTION_INTERRUPTED)

    admin = AdminMemoryCommands(host, FailedExecution())
    admin.prepare(
        ctx,
        AdminMemoryCommand(
            action="create", tenant_id="old-tenant", user_id="old-user", text="remember"
        ),
    )
    result = admin.read(ctx, ctx.operation_id, "old-tenant", "old-user")
    assert result["status"] == "failed"
    assert result["error_code"] == "EXECUTION_INTERRUPTED"
    assert "unsafe failure detail" not in str(result)


def test_unconfirmed_preparation_allows_only_same_id_retry(tmp_path):
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )

    _, ctx, _, host = fixture(tmp_path)
    admin = AdminMemoryCommands(
        host, SimpleNamespace(lookup_operation=lambda *args: SimpleNamespace(state="unconfirmed"))
    )
    admin.prepare(
        ctx,
        AdminMemoryCommand(
            action="create", tenant_id="old-tenant", user_id="old-user", text="remember"
        ),
    )
    result = admin.read(ctx, ctx.operation_id, "old-tenant", "old-user")
    assert result["retry_allowed"] is True
    assert result["operation_id"] == ctx.operation_id


def test_recall_history_is_target_scoped_paginated_and_does_not_expose_pack(tmp_path):
    from test_p3_admin_diagnostics import seed_recall

    from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics

    identity, ctx, _, host = fixture(tmp_path)
    seed_recall(host, seed(host), False)
    with identity.uow.transaction() as tx:
        row = tx.read("recall_requests", "recall1")
        row["request"] = {"query": "target query"}
        tx.write("recall_requests", "recall1", row)
        foreign = {
            **row,
            "record": {
                **row["record"],
                "recall_id": "foreign",
                "scope": {**row["record"]["scope"], "user_id": "intruder"},
            },
        }
        tx.write("recall_requests", "foreign", foreign)
    result = AdminDiagnostics(host, None).recalls(ctx, "old-tenant", "old-user")
    assert [item["recall_id"] for item in result["items"]] == ["recall1"]
    assert result["items"][0]["query"] == "target query"
    assert "private memory body" not in str(result)


def pipeline_fixture(tmp_path):
    """Real domain, task and ingress code; only storage transport is in memory."""
    from aether_agent_memory.recall.basic.service import Recall
    from aether_agent_memory.remember.basic.pipeline import RememberPipeline
    from aether_agent_memory.runtime.foundation.events import Events
    from aether_agent_memory.runtime.foundation.tasks import Tasks
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.ingress import (
        CommandAdmission,
        InputStore,
        register_ingress,
    )
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
    from aether_agent_memory.runtime.temporal.recall import RecallAdmission
    from aether_agent_memory.runtime.temporal.registry import StageRegistry
    from aether_agent_memory.runtime.temporal.service import TemporalService

    identity, ctx, state, host = fixture(tmp_path)
    tasks, events = Tasks(identity.uow, identity), Events(identity.uow, identity)
    host.remember = RememberPipeline(
        identity.uow,
        identity,
        tasks,
        events,
        None,
        None,
        None,
        "model",
        bodies=host.remember.bodies,
        tokenizer=SimpleNamespace(identifier="test"),
    )
    host.recall = Recall(
        identity.uow,
        identity,
        events,
        host.remember,
        None,
        None,
        "model",
        tokenizer=SimpleNamespace(identifier="test"),
    )
    execution = object.__new__(TemporalService)
    execution.runtime = host
    execution.require_ready = lambda: None  # No external Worker execution in admission tests.
    execution.ledger = ExecutionLedger(
        tasks, TemporalConfiguration(deployment_id="admin-test", endpoint="127.0.0.1:7233")
    )
    execution.inputs = InputStore(identity.uow, identity, tmp_path / "inputs")
    register_ingress(StageRegistry(), execution.ledger, execution.inputs, host.remember)
    execution.commands = CommandAdmission(
        execution.ledger, execution.inputs, execution.client_targets
    )
    execution.recall = RecallAdmission(execution.ledger, host.recall)
    tasks.on_admitted = lambda tx, task: execution.ledger.bind_admitted(tx, task)
    return identity, ctx, state, host, execution


@pytest.mark.parametrize("action", ["create", "update", "recall"])
def test_real_ingress_admits_once_with_actor_proof_and_target_scope(tmp_path, action):
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )

    identity, ctx, _, host, execution = pipeline_fixture(tmp_path)
    seed(host)
    admin = AdminMemoryCommands(host, execution)
    command = AdminMemoryCommand(
        action=action,
        tenant_id="old-tenant",
        user_id="old-user",
        text="test content",
        memory_id="m1" if action == "update" else None,
        expected_version=1 if action == "update" else None,
        expected_object_revision=1 if action == "update" else None,
    )
    row = admin.prepare(ctx, command)
    admin.dispatch(row)
    admin.dispatch(row)
    stored = admin.load(ctx, ctx.operation_id, "old-tenant", "old-user")
    assert stored["job_id"] is not None, stored
    with identity.uow.transaction() as tx:
        jobs = tx.rows("tasks")
        assert len(jobs) == 1
        task = jobs[0][1]
        assert task["record"]["initiator_id"] == ctx.principal.principal_id
        assert task["context"]["principal"]["home_scope"]["user_id"] == "old-user"
        assert task["context"]["principal"]["home_scope"]["tenant_id"] == "old-tenant"
        assert len(tx.rows("temporal_bindings")) == 1


@pytest.mark.parametrize("action,expected", [("archive", "archived"), ("delete", "deleted")])
def test_real_mutation_commits_versioned_state_and_preserves_cleanup_boundary(
    tmp_path, action, expected
):
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )

    identity, ctx, _, host, execution = pipeline_fixture(tmp_path)
    seed(host)
    admin = AdminMemoryCommands(host, execution)
    row = admin.prepare(
        ctx,
        AdminMemoryCommand(
            action=action,
            tenant_id="old-tenant",
            user_id="old-user",
            memory_id="m1",
            expected_version=1,
            expected_object_revision=1,
        ),
    )
    admin.dispatch(row)
    result = admin.read(ctx, ctx.operation_id, "old-tenant", "old-user")
    assert result["status"] == "succeeded", result
    with identity.uow.transaction() as tx:
        memory = host.remember.current(tx, "m1")
        assert memory.status == expected
        assert memory.object_revision == 2
        events = tx.rows("outbox")
        assert events
    if action == "delete":
        assert result["result"]["blocked"] is True
        assert result["result"]["cleanup_state"] == "pending"


def test_worker_rehydrates_bound_actor_and_rechecks_revocation(tmp_path):
    from aether_agent_memory.runtime.flows.admin_memory_commands import (
        AdminMemoryCommand,
        AdminMemoryCommands,
    )
    from aether_agent_memory.runtime.foundation.common import later, now
    from aether_agent_memory.runtime.temporal.activities import Activities

    identity, ctx, state, host, execution = pipeline_fixture(tmp_path)
    admin = AdminMemoryCommands(host, execution)
    row = admin.prepare(
        ctx,
        AdminMemoryCommand(
            action="create", tenant_id="old-tenant", user_id="old-user", text="remember"
        ),
    )
    admin.dispatch(row)
    stored = admin.load(ctx, ctx.operation_id, "old-tenant", "old-user")
    activities = object.__new__(Activities)
    activities.tasks = execution.ledger.tasks
    with identity.uow.transaction() as tx:
        _, task = execution.ledger.tasks.load(tx, stored["job_id"])
        restored = activities.authorized_context(tx, task, later(now(), 600))
        assert restored.principal.principal_id == ctx.principal.principal_id
        assert restored.principal.home_scope.user_id == "old-user"
    state["permissions"].remove("aether:memory:create")
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        activities.authorized_context(tx, task, later(now(), 600))


def test_http_contract_requires_operation_id_and_returns_durable_pending(tmp_path):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from aether_agent_memory.runtime.flows.admin_memory_commands import attach

    _, ctx, _, host, execution = pipeline_fixture(tmp_path)
    app = FastAPI()
    app.state.trusted_dependency = Depends(lambda: ctx)
    attach(app, host, execution)
    body = {
        "action": "create",
        "tenant_id": "old-tenant",
        "user_id": "old-user",
        "text": "remember",
    }
    with TestClient(app) as client:
        assert client.post("/p3/admin/memory-commands", json=body).status_code == 422
        invalid = client.post(
            "/p3/admin/memory-commands",
            json={**body, "principal_id": "spoof"},
            headers={"X-Operation-ID": ctx.operation_id},
        )
        assert invalid.status_code == 422
        response = client.post(
            "/p3/admin/memory-commands", json=body, headers={"X-Operation-ID": ctx.operation_id}
        )
        assert response.status_code == 202
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["status"] == "pending"
        assert response.json()["operation_id"] == ctx.operation_id
