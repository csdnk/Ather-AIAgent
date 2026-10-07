from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi import HTTPException
from tests.platform.test_operations import TestActor
from tests.platform.test_operations_store import store  # noqa: F401

from aether_platform.operations.models import Command
from aether_platform.operations.service import Operations


def operator(name="operator", permissions=("*:*:*",)):
    return TestActor(name, "https://id", name, None, "platform_admin", 1, permissions)


def detail(state="failed", effect="not_started", tenant="tenant-a"):
    return {
        "task": {
            "task_id": "task-one",
            "revision": 2,
            "kind": "remember.project",
            "state": state,
            "effect_status": effect,
            "error_code": "DEPENDENCY_UNAVAILABLE",
            "subject": {
                "object_id": "memory-one",
                "scope": {
                    "tenant_id": tenant,
                    "user_id": "user-a",
                    "application_id": "app",
                    "agent_id": "agent",
                    "session_id": "session-one",
                },
            },
        },
        "workflow": {
            "status": "available",
            "state": "completed",
            "start_time": "2026-10-06T01:00:00+00:00",
            "close_time": "2026-10-06T01:01:00+00:00",
        },
        "progress": {"wait": {"dependency_id": "vector-index", "reason_code": "UNAVAILABLE"}},
        "binding": {"stage": "project"},
        "status": "available",
    }


@pytest.fixture
def cases(store):  # noqa: F811
    from aether_platform.operations.task_cases import TaskCases

    ops = object.__new__(Operations)
    ops.store, ops.directory, ops.config = store, store.directory, {}
    snapshots = {"task-one": detail(), "task-two": detail("succeeded", "confirmed")}
    snapshots["task-two"]["task"]["task_id"] = "task-two"
    snapshots["task-two"]["workflow"]["start_time"] = "2026-10-07T01:00:00+00:00"
    snapshots["task-two"]["workflow"]["close_time"] = "2026-10-07T01:01:00+00:00"
    ops.p3_call = lambda actor, token, method, path, **kw: deepcopy(snapshots[path.split("/")[-1]])
    service = TaskCases(ops)
    sequence = 0

    def run(action, version=None, params=None, who=None, command_id=None):
        nonlocal sequence
        sequence += 1
        cmd = Command(
            command_id=command_id or f"test-case-{sequence}",
            resource="support",
            action="case_" + action,
            target_id="task-one",
            expected_version=version,
            parameters=params or {},
        )
        return service.command(who or operator(), "valid", cmd)

    return service, run, snapshots, ops


def test_complete_case_lifecycle_with_replacement_and_reopen(cases):
    service, run, snapshots, _ = cases
    opened = run("open")
    assert opened["status"] == "complete"
    row = service.read(operator(), task_id="task-one")["items"][0]
    assert row["state"] == "open" and row["tenant_id"] == "tenant-a"
    run("claim", row["version"])
    run("note", 2, {"note": "索引依赖恢复，已在原业务中补建索引"})
    run(
        "resolve",
        3,
        {
            "note": "原记忆已补建索引，关联新任务进行核验",
            "kind": "replacement",
            "replacement_task_id": "task-two",
        },
    )
    run("verify", 4, {"note": "核对原对象与补建索引执行结果"})
    run("close", 5)
    row = service.read(operator(), task_id="task-one")["items"][0]
    assert row["state"] == "closed" and len(row["history"]) == 6
    assert row["verification"]["passed"] is True
    assert snapshots["task-one"]["task"]["state"] == "failed"
    run("reopen", 6, {"note": "后续发现同一问题再次出现，重新排查"})
    assert service.read(operator(), task_id="task-one")["items"][0]["state"] == "open"


def test_claim_conflict_idempotence_and_no_unverified_close(cases):
    service, run, _, _ = cases
    run("open", command_id="same-command-1")
    assert run("open", command_id="same-command-1")["status"] == "complete"
    assert service.read(operator(), task_id="task-one")["items"][0]["version"] == 1
    run("claim", 1)
    with pytest.raises(HTTPException):
        run("claim", 1, who=operator("other"))
    with pytest.raises(HTTPException):
        run("close", 2)
    with pytest.raises(HTTPException):
        run("note", 2, {"note": "未经负责人授权编辑"}, who=operator("other"))


def test_tenant_and_permission_boundaries(cases):
    service, run, _, _ = cases
    tenant = replace(operator(), role="tenant_admin", tenant_id="tenant-a")
    with pytest.raises(HTTPException) as exc:
        service.read(tenant, task_id="task-one")
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        run("open", who=operator(permissions=("aether:ops:read",)))
    with pytest.raises(HTTPException):
        run("open", who=tenant)


def test_replacement_scope_and_actual_business_success_required(cases):
    service, run, snapshots, _ = cases
    run("open")
    run("claim", 1)
    run(
        "resolve",
        2,
        {
            "note": "关联补办任务，等待核实执行结果",
            "kind": "replacement",
            "replacement_task_id": "task-two",
        },
    )
    snapshots["task-two"]["task"]["subject"]["scope"]["tenant_id"] = "another-tenant"
    run("verify", 3, {"note": "核对补办任务范围和执行回执"})
    row = service.read(operator(), task_id="task-one")["items"][0]
    assert row["state"] == "awaiting_verification" and not row["verification"]["passed"]
    with pytest.raises(HTTPException):
        run("close", 4)


def test_manual_closure_requires_other_reviewer_and_known_effect(cases):
    service, run, snapshots, _ = cases
    run("open")
    run("claim", 1)
    run("resolve", 2, {"kind": "no_longer_needed", "note": "业务负责人确认历史评估无需继续办理"})
    with pytest.raises(HTTPException):
        run("verify", 3, {"note": "本人直接复核自己的结案决定", "confirmed": True})
    snapshots["task-one"]["task"]["effect_status"] = "unknown"
    run(
        "verify",
        3,
        {"note": "独立复核原业务效果与结案依据", "confirmed": True},
        operator("reviewer"),
    )
    assert not service.read(operator(), task_id="task-one")["items"][0]["verification"]["passed"]


def test_control_unknown_blocks_second_operation_and_polls_original(cases):
    service, run, snapshots, ops = cases
    snapshots["task-one"] = detail("recovery_wait", "unknown")
    snapshots["task-one"]["workflow"]["state"] = "running"
    calls = []

    def control(actor, token, cmd):
        calls.append(cmd)
        return {"status": "unknown", "id": cmd.command_id, "result": {}}

    ops.command = control
    ops.command_status = lambda a, t, cid: {"items": [{"status": "complete", "id": cid}]}
    run("open")
    run("claim", 1)
    run(
        "control",
        2,
        {"action": "reconcile", "note": "依赖已恢复，核对原执行"},
        command_id="control-case-1",
    )
    with pytest.raises(HTTPException):
        run("control", 4, {"action": "cancel", "note": "重复发送另外一个操作"})
    assert len(calls) == 1
    run("poll", 4)
    row = service.read(operator(), task_id="task-one")["items"][0]
    assert row["control"]["status"] == "complete"
    assert row["control"]["command_id"] == calls[0].command_id


def test_closure_rechecks_evidence_revision(cases):
    _, run, snapshots, _ = cases
    run("open")
    run("claim", 1)
    run(
        "resolve",
        2,
        {
            "kind": "replacement",
            "replacement_task_id": "task-two",
            "note": "已经补建索引，关联后续任务核验",
        },
    )
    run("verify", 3, {"note": "核对作用域对象与业务完成回执"})
    snapshots["task-two"]["task"]["revision"] += 1
    with pytest.raises(HTTPException):
        run("close", 4)


def test_check_takeover_and_manual_close_with_independent_reviewer(cases):
    service, run, _, _ = cases
    run("open")
    run("claim", 1)
    run("check", 2)
    row = service.read(operator(), task_id="task-one")["items"][0]
    assert row["diagnosis"]["checks"][0]["result"] == "未找到对应记录"
    run("escalate", 3, {"note": "索引依赖异常，请研发接手继续定位"})
    engineer = operator("engineer")
    run("claim", 4, who=engineer)
    run(
        "resolve",
        5,
        {"kind": "no_longer_needed", "note": "业务负责人已确认历史任务无需继续执行"},
        engineer,
    )
    run("verify", 6, {"note": "独立核查原业务结果与结案依据", "confirmed": True})
    run("close", 7)
    assert service.read(operator(), task_id="task-one")["items"][0]["state"] == "closed"


def test_notes_remain_available_when_p3_is_down_and_control_needs_separate_grant(cases):
    _, run, _, ops = cases
    run("open")
    run("claim", 1)
    from aether_platform.p3 import P3Error

    def down(*args, **kwargs):
        raise P3Error("CONNECTION_UNCONFIRMED")

    ops.p3_call = down
    assert run("note", 2, {"note": "服务暂时不可连接，正在排查网络"})["status"] == "complete"
    with pytest.raises(HTTPException) as exc:
        run(
            "control",
            3,
            {"action": "reconcile", "note": "尝试核对任务执行进展"},
            operator(permissions=("aether:ops:read", "aether:support:execute")),
        )
    assert exc.value.status_code == 403


def test_original_success_path_does_not_execute_business_again(cases):
    service, run, snapshots, _ = cases
    snapshots["task-one"] = detail("succeeded", "confirmed")
    run("open")
    run("claim", 1)
    run("resolve", 2, {"kind": "original_success", "note": "核查发现原任务已成功，不需要重复执行"})
    run("verify", 3, {"note": "核验原工作流与业务回执均已完成"})
    run("close", 4)
    result = service.read(operator(), task_id="task-one")["items"][0]
    assert result["state"] == "closed" and "control" not in result


def test_old_success_and_different_object_version_cannot_close_new_failure(cases):
    service, run, snapshots, _ = cases
    run("open")
    run("claim", 1)
    run(
        "resolve",
        2,
        {
            "kind": "replacement",
            "replacement_task_id": "task-two",
            "note": "关联后续任务检查是否确实修复问题",
        },
    )
    snapshots["task-two"]["workflow"]["start_time"] = "2026-10-05T01:00:00+00:00"
    run("verify", 3, {"note": "检查后续任务执行时间与对象版本"})
    assert not service.read(operator(), task_id="task-one")["items"][0]["verification"]["passed"]
    snapshots["task-two"]["workflow"]["start_time"] = "2026-10-07T01:00:00+00:00"
    snapshots["task-two"]["task"]["subject"]["version"] = "another-version"
    run("verify", 4, {"note": "再次检查后续任务的对象版本是否相同"})
    assert not service.read(operator(), task_id="task-one")["items"][0]["verification"]["passed"]


def test_admitted_but_not_sent_control_resumes_same_id_after_missing_lookup(cases):
    service, run, snapshots, ops = cases
    from aether_platform.p3 import P3Error

    snapshots["task-one"] = detail("running", "unknown")
    snapshots["task-one"]["workflow"]["state"] = "running"
    real_command, calls = ops.command, []

    def crash_after_admission(actor, token, command):
        ops.store.admit(actor, command)
        raise RuntimeError("simulated process exit before send")

    ops.command = crash_after_admission
    run("open")
    run("claim", 1)
    run("control", 2, {"action": "reconcile", "note": "依赖恢复，继续核对原任务执行状态"})
    row = service.read(operator(), task_id="task-one")["items"][0]
    cid = row["control"]["command_id"]

    def upstream(actor, token, method, path, **kwargs):
        if "/admin/tasks/" in path:
            return deepcopy(snapshots["task-one"])
        if method == "GET":
            if calls:
                return {"operation_id": cid, "state": "completed"}
            raise P3Error("NOT_FOUND")
        calls.append(kwargs["body"])
        return {"operation_id": cid, "state": "accepted"}

    ops.p3_call, ops.command = upstream, real_command
    run("poll", 4)
    run("poll", 5)
    assert len(calls) == 1 and calls[0]["operation_id"] == cid
    assert (
        service.read(operator(), task_id="task-one")["items"][0]["control"]["status"] == "complete"
    )
