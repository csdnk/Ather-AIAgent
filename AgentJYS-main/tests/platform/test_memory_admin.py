from dataclasses import replace

import pytest
from fastapi import HTTPException
from tests.platform.test_operations import TestActor
from tests.platform.test_operations_store import store  # noqa: F401

from aether_platform.operations.models import Command
from aether_platform.operations.service import Operations
from aether_platform.p3 import P3Error


def actor(tenant=None, permissions=("*:*:*",)):
    return TestActor(
        "admin-" + str(tenant),
        "issuer",
        "subject",
        tenant,
        "tenant_admin" if tenant else "platform_admin",
        1,
        permissions,
    )


@pytest.fixture
def management(store):  # noqa: F811
    from aether_platform.operations.memory_admin import MemoryAdmin

    store.directory.seed(
        "test",
        [{"id": t, "name": t, "enabled": True} for t in ("a", "b")],
        [
            {
                "id": "u-" + t,
                "issuer": "issuer",
                "subject": "u-" + t,
                "username": "u-" + t,
                "display_name": t,
                "tenant_id": t,
                "role": "user",
                "enabled": True,
            }
            for t in ("a", "b")
        ],
    )
    ops = object.__new__(Operations)
    ops.directory, ops.store, ops.config = store.directory, store, {}
    admin = MemoryAdmin(ops)
    calls, results = [], {}

    def upstream(who, token, method, operation_id, *, body=None, target=None):
        calls.append((method, operation_id, body))
        if method == "POST":
            results[operation_id] = {"status": "pending", "job_id": "job-one"}
        if operation_id not in results:
            raise P3Error("NOT_FOUND")
        return results[operation_id]

    admin.upstream = upstream
    return admin, ops, calls, results


def command(action, cid="memory-command-1", target=None, version=None, **params):
    return Command(
        command_id=cid,
        resource="memories",
        action=action,
        target_id=target,
        expected_version=version,
        parameters={"user_id": "u-a", **params},
    )


def test_scheme_crud_cas_idempotence_and_target_isolation(management):
    admin, _, _, _ = management
    create = command("recall_scheme_create", name="客户偏好", query="查询客户沟通偏好")
    first = admin.command(actor("a"), "token", create)
    assert first["status"] == "succeeded"
    sid = first["result"]["id"]
    assert admin.command(actor("a"), "token", create) == first
    assert admin.read(actor("a"), "u-a", "schemes")["total"] == 1
    with pytest.raises(HTTPException) as denied:
        admin.read(actor("b"), "u-a", "schemes")
    assert denied.value.status_code == 404
    update = command(
        "recall_scheme_update", "memory-command-2", sid, 1, name="更新方案", query="新的查询条件"
    )
    assert admin.command(actor("a"), "token", update)["result"]["version"] == 2
    with pytest.raises(HTTPException) as conflict:
        admin.command(
            actor("a"), "token", update.model_copy(update={"command_id": "stale-command"})
        )
    assert conflict.value.status_code == 409
    admin.command(actor("a"), "token", command("recall_scheme_delete", "delete-command", sid, 2))
    assert admin.read(actor("a"), "u-a", "schemes")["total"] == 0
    assert admin.read(actor("a"), "u-a", "history")["total"] == 0


def test_fine_permissions_and_scope_injection_fail_before_admission(management):
    admin, ops, calls, _ = management
    minimal = ("aether:ops:read", "aether:content:read", "aether:memories:execute")
    for who in (actor("a", minimal), replace(actor("a"), role="user"), actor("b")):
        with pytest.raises(HTTPException):
            admin.command(who, "token", command("memory_create", text="new fact"))
    with pytest.raises(HTTPException):
        admin.command(actor("a"), "token", command("memory_create", text="fact", tenant_id="b"))
    assert not calls
    assert ops.store.commands(actor())["total"] == 0


def test_real_target_stable_replay_and_content_not_in_generic_audit(management):
    admin, ops, calls, results = management
    cmd = command("memory_create", text="private body")
    assert admin.command(actor("a"), "token", cmd)["status"] == "pending"
    body = calls[0][2]
    assert body["tenant_id"] == "a" and body["user_id"] == "u-a"
    assert admin.command(actor("a"), "token", cmd)["status"] == "pending"
    assert [c[0] for c in calls] == ["POST", "GET"]
    results[cmd.command_id] = {"status": "succeeded", "result": {"memories": [{"memory_id": "m1"}]}}
    assert admin.status(actor("a"), "token", cmd.command_id)["status"] == "succeeded"
    with pytest.raises(HTTPException):
        admin.command(
            actor("a"),
            "token",
            cmd.model_copy(update={"parameters": {"user_id": "u-a", "text": "changed"}}),
        )
    assert "private body" not in str(ops.store.commands(actor()))


def test_crash_before_post_resumes_exact_persisted_request_and_revocation_blocks(management):
    admin, _, calls, _ = management
    original = admin.upstream
    admin.upstream = lambda *a, **kw: (_ for _ in ()).throw(P3Error("CONNECTION_UNCONFIRMED"))
    cmd = command("memory_create", text="one original request")
    assert admin.command(actor("a"), "token", cmd)["status"] == "pending"
    admin.upstream = original
    assert admin.status(actor("a"), "token", cmd.command_id)["status"] == "pending"
    assert [c[0] for c in calls] == ["GET", "POST"]
    assert calls[-1][1] == cmd.command_id and calls[-1][2]["text"] == "one original request"
    with pytest.raises(HTTPException):
        admin.status(
            actor("a", ("aether:ops:read", "aether:content:read")), "token", cmd.command_id
        )
    assert len(calls) == 2


def test_delete_requires_complete_versions_and_result_reauthorized(management):
    admin, _, calls, _ = management
    with pytest.raises(HTTPException):
        admin.command(actor("a"), "token", command("memory_delete", target="m1", version=1))
    cmd = command("memory_delete", target="m1", version=1, expected_object_revision=3)
    assert admin.command(actor("a"), "token", cmd)["status"] == "pending"
    assert calls[0][2]["expected_object_revision"] == 3
    with pytest.raises(HTTPException):
        admin.status(actor("b"), "token", cmd.command_id)


def test_saved_scheme_execute_freezes_query_and_scope(management):
    admin, _, calls, _ = management
    saved = admin.command(
        actor("a"), "token", command("recall_scheme_create", name="事实检索", query="原始问题")
    )
    sid = saved["result"]["id"]
    result = admin.command(
        actor("a"), "token", command("recall_execute", "execute-command", sid, 1, scheme_id=sid)
    )
    assert result["status"] == "pending"
    assert calls[0][2]["text"] == "原始问题"
    assert admin.read(actor("a"), "u-a", "history")["total"] == 1
    assert admin.read(actor("b"), "u-b", "history")["total"] == 0


def test_saved_scheme_execution_rejects_changed_version(management):
    admin, _, calls, _ = management
    saved = admin.command(
        actor("a"), "token", command("recall_scheme_create", name="事实检索", query="原始问题")
    )
    sid = saved["result"]["id"]
    admin.command(
        actor("a"),
        "token",
        command(
            "recall_scheme_update", "update-command", sid, 1, name="新方案", query="已改变问题"
        ),
    )
    with pytest.raises(HTTPException) as error:
        admin.command(
            actor("a"), "token", command("recall_execute", "execute-command", sid, 1, scheme_id=sid)
        )
    assert error.value.status_code == 409 and calls == []


def test_pending_preparation_retries_same_id_and_conflict_is_terminal(management):
    admin, _, calls, results = management
    cmd = command("memory_create", text="原始事实")
    admin.command(actor("a"), "token", cmd)
    results[cmd.command_id] = {"status": "pending", "retry_allowed": True}
    admin.status(actor("a"), "token", cmd.command_id)
    assert calls[-2][0] == "GET" and calls[-1][0] == "POST"
    assert calls[-1][1:] == calls[0][1:]
    admin.upstream = lambda *a, **kw: (_ for _ in ()).throw(P3Error("VERSION_CONFLICT"))
    assert admin.status(actor("a"), "token", cmd.command_id)["status"] == "failed"


def test_abandon_unadmitted_request_fences_late_post_but_not_accepted_work(management):
    admin, _, calls, _ = management
    original = command("memory_create", text="delayed request")
    abort = command("memory_abandon", original_action="memory_create")
    assert admin.command(actor("a"), "token", abort)["status"] == "abandoned"
    with pytest.raises(HTTPException) as rejected:
        admin.command(actor("a"), "token", original)
    assert rejected.value.status_code == 409 and calls == []
    accepted = original.model_copy(update={"command_id": "accepted-original"})
    admin.command(actor("a"), "token", accepted)
    already = abort.model_copy(update={"command_id": accepted.command_id})
    assert admin.command(actor("a"), "token", already)["status"] == "pending"
    assert [c[0] for c in calls] == ["POST", "GET"]
