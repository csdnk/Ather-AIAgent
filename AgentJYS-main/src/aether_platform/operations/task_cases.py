"""Task-linked operator cases; immutable business history and verified case closure."""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Never, TypedDict, cast

from fastapi import HTTPException
from psycopg import Connection
from psycopg.types.json import Jsonb

from aether_platform.directory import Actor
from aether_platform.operations.models import Command, now, page
from aether_platform.p3 import P3Error, identifier

if TYPE_CHECKING:
    from aether_platform.operations.service import Operations


class TaskWorkflow(TypedDict):
    status: str | None
    state: str | None
    start_time: str | None
    close_time: str | None


class TaskSnapshot(TypedDict):
    task_id: str
    revision: int
    kind: str | None
    state: str | None
    effect_status: str | None
    error_code: str | None
    subject: dict[str, Any]
    workflow: TaskWorkflow
    stage: str | None
    wait: dict[str, Any]


class Verification(TypedDict):
    passed: bool
    reason: str
    reviewer_id: str
    note: str
    at: str
    evidence_hash: str


TERMINAL = {"failed", "attention_required", "succeeded", "cancelled"}
ACTIVE = {"pending", "queued", "running", "retry_wait", "recovery_wait"}
DONE = {"complete", "failed", "rejected"}
ACTIONS = {
    "open",
    "claim",
    "note",
    "escalate",
    "check",
    "resolve",
    "verify",
    "close",
    "reopen",
    "control",
    "poll",
}


def require(actor: Actor, permission: str = "aether:ops:read") -> None:
    permissions = getattr(actor, "permissions", ())
    if actor.role != "platform_admin" or (
        "*:*:*" not in permissions
        and (permission not in permissions or "aether:ops:read" not in permissions)
    ):
        raise HTTPException(403, detail={"code": "CASE_PERMISSION_REQUIRED"})


def fail(code: str) -> Never:
    raise HTTPException(409, detail={"code": code})


def note(parameters: Mapping[str, object]) -> str:
    value = parameters.get("note")
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 2000:
        fail("CASE_NOTE_REQUIRED")
    return value.strip()


def snapshot(data: Mapping[str, Any]) -> TaskSnapshot:
    task = data.get("task", {})
    task_id, revision = task.get("task_id"), task.get("revision")
    if not isinstance(task_id, str) or not task_id or type(revision) is not int:
        fail("CASE_TASK_UNAVAILABLE")
    return {
        "task_id": task_id,
        "revision": revision,
        "kind": task.get("kind"),
        "state": task.get("state"),
        "effect_status": task.get("effect_status"),
        "error_code": task.get("error_code"),
        "subject": task.get("subject", {}),
        "workflow": {
            "status": data.get("workflow", {}).get("status"),
            "state": data.get("workflow", {}).get("state"),
            "start_time": data.get("workflow", {}).get("start_time"),
            "close_time": data.get("workflow", {}).get("close_time"),
        },
        "stage": data.get("binding", {}).get("stage"),
        "wait": {
            k: data.get("progress", {}).get("wait", {}).get(k)
            for k in ("dependency_id", "reason_code", "next_check_at")
        },
    }


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def scope(task: TaskSnapshot) -> dict[str, Any]:
    return cast(dict[str, Any], task.get("subject", {}).get("scope", {}))


def controlled(task: TaskSnapshot) -> bool:
    return (
        task["state"] in ACTIVE
        and task["revision"] > 0
        and task["workflow"].get("status") == "available"
        and task["workflow"].get("state") == "running"
    )


def completed(task: TaskSnapshot) -> bool:
    return (
        task["state"] == "succeeded"
        and task["effect_status"] == "confirmed"
        and task["workflow"].get("status") == "available"
        and task["workflow"].get("state") == "completed"
    )


def follows(original: TaskSnapshot, replacement: TaskSnapshot) -> bool:
    try:
        close_time = original["workflow"]["close_time"]
        start_time = replacement["workflow"]["start_time"]
        if close_time is None or start_time is None:
            return False
        ended = datetime.fromisoformat(close_time)
        started = datetime.fromisoformat(start_time)
        return ended.tzinfo is not None and started.tzinfo is not None and started >= ended
    except (KeyError, TypeError, ValueError):
        return False


class TaskCases:
    def __init__(self, operations: "Operations") -> None:
        self.ops, self.store = operations, operations.store

    def task(self, actor: Actor, token: str | Callable[[], str], task_id: str) -> TaskSnapshot:
        data = self.ops.p3_call(actor, token, "GET", "/p3/admin/tasks/" + identifier(task_id))
        result = snapshot(data)
        if result["task_id"] != task_id:
            fail("CASE_TASK_MISMATCH")
        return result

    def read(
        self,
        actor: Actor,
        *,
        task_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        require(actor)
        with self.ops.directory.connection() as conn:
            clause = (
                "(%s::text IS NULL OR task_id=%s) AND (%s::text IS NULL OR payload->>'state'=%s)"
            )
            args = (task_id, task_id, state, state)
            count_row = conn.execute(
                "SELECT count(*) AS n FROM ops_task_cases WHERE " + clause, args
            ).fetchone()
            assert count_row is not None
            rows = conn.execute(
                "SELECT * FROM ops_task_cases WHERE "
                + clause
                + " ORDER BY updated_at DESC,task_id LIMIT %s OFFSET %s",
                (*args, limit, offset),
            ).fetchall()
            counts = conn.execute(
                "SELECT payload->>'state' AS state,count(*) AS n FROM ops_task_cases GROUP BY 1"
            ).fetchall()
            people = {event["actor_id"] for row in rows for event in row["payload"]["history"]}
            names = (
                {
                    r["id"]: r["display_name"]
                    for r in conn.execute(
                        "SELECT id,display_name FROM users WHERE id=ANY(%s)", (list(people),)
                    ).fetchall()
                }
                if people
                else {}
            )
        for row in rows:
            value = row["payload"]
            value["owner_name"] = names.get(
                value.get("owner_id"), "未认领" if not value.get("owner_id") else "平台处理人员"
            )
            for event in value["history"]:
                event["actor_name"] = names.get(event["actor_id"], "平台处理人员")
        result = page(
            [
                {**r["payload"], "version": r["version"], "updated_at": r["updated_at"]}
                for r in rows
            ],
            count_row["n"],
            by_state={r["state"]: r["n"] for r in counts},
            actor_id=actor.id,
        )
        from aether_platform.operations.presentation import attach_names

        return attach_names(result, "support", actor, self.ops.directory)

    def load(self, conn: Connection[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("task-case:" + task_id,)
        )
        return conn.execute(
            "SELECT * FROM ops_task_cases WHERE task_id=%s FOR UPDATE", (task_id,)
        ).fetchone()

    def save(
        self,
        conn: Connection[dict[str, Any]],
        task_id: str,
        payload: dict[str, Any],
        old: dict[str, Any] | None,
    ) -> dict[str, Any]:
        row = conn.execute(
            "INSERT INTO ops_task_cases(task_id,tenant_id,payload) VALUES (%s,%s,%s) "
            "ON CONFLICT(task_id) DO UPDATE SET payload=EXCLUDED.payload,"
            "version=ops_task_cases.version+1,updated_at=now() RETURNING *",
            (task_id, payload["tenant_id"], Jsonb(payload)),
        ).fetchone()
        assert row is not None
        return row

    def verify(
        self,
        actor: Actor,
        parameters: Mapping[str, object],
        payload: dict[str, Any],
        original: TaskSnapshot,
        replacement: TaskSnapshot | None,
    ) -> Verification:
        resolution = payload["resolution"]
        kind = resolution["kind"]
        if kind == "no_longer_needed":
            if actor.id == payload["owner_id"] or parameters.get("confirmed") is not True:
                fail("CASE_INDEPENDENT_REVIEW_REQUIRED")
            passed = (
                original["state"] in TERMINAL
                and original["effect_status"] in {"confirmed", "no_effect", "not_started"}
                and original["workflow"]["status"] == "available"
                and original["workflow"]["state"] != "running"
            )
            reason = (
                "人工复核无需继续办理；保留原任务结果" if passed else "原业务结果或工作流仍未确认"
            )
        elif kind == "replacement":
            passed = bool(
                replacement
                and completed(replacement)
                and original["state"] in TERMINAL
                and original["workflow"]["status"] == "available"
                and original["workflow"]["state"] != "running"
                and original["effect_status"] in {"confirmed", "no_effect", "not_started"}
                and scope(original) == scope(replacement)
                and original["kind"] == replacement["kind"]
                and original["subject"] == replacement["subject"]
                and follows(original, replacement)
            )
            reason = (
                "同一业务对象的后续任务已确认成功"
                if passed
                else "后续任务未成功、对象版本或时间不符，或原结果未知"
            )
        else:
            passed = completed(original)
            reason = (
                "原任务及业务回执均已确认成功" if passed else "原任务尚无成功且已确认的业务回执"
            )
        return {
            "passed": passed,
            "reason": reason,
            "reviewer_id": actor.id,
            "note": note(parameters),
            "at": now(),
            "evidence_hash": digest([original, replacement]),
        }

    def command(
        self, actor: Actor, token: str | Callable[[], str], command: Command
    ) -> dict[str, Any]:
        require(actor, "aether:support:execute")
        action = command.action.removeprefix("case_")
        if action not in ACTIONS or not command.target_id:
            fail("CASE_ACTION_INVALID")
        params = command.parameters
        allowed = {"note", "kind", "replacement_task_id", "confirmed", "action"}
        if set(params) - allowed or len(json.dumps(params)) > 12000:
            fail("CASE_ACTION_INVALID")
        if action in {"control", "poll"}:
            require(actor, "aether:tasks:execute")
        # An acknowledged or uncertain command is never submitted a second time.
        try:
            previous = self.store.command_record(actor, command.command_id)
            if previous:
                return self.store.admit(actor, command)[0]
            existing = self.read(actor, task_id=command.target_id)["items"]
            if action != "open" and (
                not existing or existing[0]["version"] != command.expected_version
            ):
                fail("CASE_VERSION_CONFLICT")
            original = (
                self.task(actor, token, command.target_id)
                if action in {"open", "check", "verify", "close", "control"} or not existing
                else existing[0].get("latest", existing[0]["original"])
            )
            replacement = None
            resolution = (existing[0].get("resolution") or {}) if existing else {}
            if action in {"verify", "close"} and resolution.get("replacement_task_id"):
                replacement = self.task(actor, token, resolution["replacement_task_id"])
            if action == "poll" and existing and existing[0].get("control"):
                control_id = existing[0]["control"]["command_id"]
                try:
                    observed_control = self.ops.command_status(actor, token, control_id)["items"][0]
                    if observed_control.get("lookup_status") == "not_found":
                        control = existing[0]["control"]
                        if actor.id != control.get("actor_id"):
                            fail("CASE_ORIGINAL_OPERATOR_REQUIRED")
                        observed_control = self.ops.resume_task_control(
                            actor, token, Command.model_validate(control["request"])
                        )
                except HTTPException as exc:
                    control = existing[0]["control"]
                    if exc.status_code != 404 or actor.id != control.get("actor_id"):
                        raise
                    # A crash before child admission is safe to resume with the SAME
                    # persisted command: Operations.admit performs at-most-once handoff.
                    observed_control = self.ops.command(
                        actor, token, Command.model_validate(control["request"])
                    )
            else:
                observed_control = None
            with self.ops.directory.connection() as conn:
                receipt, created = self.store.admit(actor, command, connection=conn)
                if not created:
                    return receipt
                old = self.load(conn, command.target_id)
                if action == "open" and old:
                    return self.store.finish(
                        command.command_id,
                        "complete",
                        {"task_id": command.target_id, "version": old["version"]},
                        connection=conn,
                    )
                if action != "open" and (not old or old["version"] != command.expected_version):
                    fail("CASE_VERSION_CONFLICT")
                if old:
                    payload = old["payload"]
                    if (
                        scope(payload["original"]) != scope(original)
                        or payload["original"]["kind"] != original["kind"]
                    ):
                        fail("CASE_TASK_MISMATCH")
                else:
                    payload = {
                        "task_id": command.target_id,
                        "tenant_id": scope(original).get("tenant_id"),
                        "user_id": scope(original).get("user_id"),
                        "original": original,
                        "state": "open",
                        "owner_id": None,
                        "history": [],
                        "created_at": now(),
                    }
                state = payload["state"]
                if (
                    action not in {"open", "claim", "check", "verify", "close", "reopen", "poll"}
                    and payload["owner_id"] != actor.id
                ):
                    fail("CASE_OWNER_REQUIRED")
                if action not in {"open", "reopen"} and state == "closed":
                    fail("CASE_ALREADY_CLOSED")
                pending = payload.get("control", {}).get("status")
                if pending and pending not in DONE and action not in {"poll", "check", "note"}:
                    fail("CASE_CONTROL_PENDING")
                entry = {"action": action, "actor_id": actor.id, "at": now()}
                if action in {"note", "escalate", "resolve", "verify", "reopen", "control"}:
                    entry["note"] = note(params)
                if action == "claim":
                    if state not in {"open", "escalated"}:
                        fail("CASE_ALREADY_CLAIMED")
                    payload.update(state="in_progress", owner_id=actor.id)
                elif action in {"note", "escalate"}:
                    if state not in {"in_progress", "escalated"}:
                        fail("CASE_STATE_INVALID")
                    if action == "escalate":
                        payload["state"] = "escalated"
                elif action == "check":
                    payload["diagnosis"] = self.diagnose(conn, original)
                    entry["result"] = "已重新读取任务、工作流和账号状态"
                elif action == "resolve":
                    if state not in {
                        "in_progress",
                        "escalated",
                        "awaiting_verification",
                        "verified",
                    }:
                        fail("CASE_STATE_INVALID")
                    kind = params.get("kind")
                    if kind not in {"original_success", "replacement", "no_longer_needed"}:
                        fail("CASE_RESOLUTION_REQUIRED")
                    replacement_id = params.get("replacement_task_id")
                    if kind == "replacement" and (
                        not replacement_id or replacement_id == command.target_id
                    ):
                        fail("CASE_REPLACEMENT_REQUIRED")
                    if replacement_id:
                        identifier(replacement_id)
                    payload.update(
                        state="awaiting_verification",
                        verification=None,
                        resolution={
                            "kind": kind,
                            "note": entry["note"],
                            "replacement_task_id": replacement_id
                            if kind == "replacement"
                            else None,
                        },
                    )
                elif action == "verify":
                    if state not in {"awaiting_verification", "verified"}:
                        fail("CASE_STATE_INVALID")
                    if resolution != payload.get("resolution"):
                        fail("CASE_VERSION_CONFLICT")
                    payload["verification"] = self.verify(
                        actor, params, payload, original, replacement
                    )
                    payload["state"] = (
                        "verified" if payload["verification"]["passed"] else "awaiting_verification"
                    )
                    entry["result"] = payload["verification"]["reason"]
                elif action == "close":
                    verification = payload.get("verification") or {}
                    if (
                        state != "verified"
                        or not verification.get("passed")
                        or verification.get("reviewer_id") != actor.id
                        or (
                            datetime.now(UTC) - datetime.fromisoformat(verification["at"])
                        ).total_seconds()
                        > 900
                        or verification["evidence_hash"] != digest([original, replacement])
                    ):
                        fail("CASE_VERIFY_AGAIN")
                    payload.update(state="closed", closed_at=now(), closed_by=actor.id)
                elif action == "reopen":
                    if state != "closed":
                        fail("CASE_STATE_INVALID")
                    payload.update(state="open", owner_id=None, verification=None, resolution=None)
                elif action == "control":
                    if state not in {"in_progress", "escalated"} or not controlled(original):
                        fail("CASE_CONTROL_UNAVAILABLE")
                    if params.get("action") not in {"cancel", "reconcile"}:
                        fail("CASE_ACTION_INVALID")
                    child_id = "case-" + hashlib.sha256(command.command_id.encode()).hexdigest()
                    child = Command(
                        command_id=child_id,
                        resource="tasks",
                        action=params["action"],
                        target_id=command.target_id,
                        expected_version=original["revision"],
                        parameters={"reason": params["note"]},
                    )
                    payload["control"] = {
                        "command_id": child_id,
                        "action": params["action"],
                        "status": "submitted",
                        "actor_id": actor.id,
                        "request": child.model_dump(),
                    }
                    entry["command_id"] = child_id
                elif action == "poll":
                    if not observed_control or observed_control["id"] != payload.get(
                        "control", {}
                    ).get("command_id"):
                        fail("CASE_CONTROL_UNAVAILABLE")
                    payload["control"]["status"] = observed_control["status"]
                    entry["result"] = observed_control["status"]
                payload["latest"] = original
                payload["history"].append(entry)
                saved = self.save(conn, command.target_id, payload, old)
                result = {
                    "task_id": command.target_id,
                    "version": saved["version"],
                    "state": payload["state"],
                }
                receipt = self.store.finish(command.command_id, "complete", result, connection=conn)
            if action == "control":
                try:
                    submitted = self.ops.command(actor, token, child)
                    status = submitted["status"]
                except (HTTPException, RuntimeError, OSError):
                    status = "unknown"
                with self.ops.directory.connection() as conn:
                    current = self.load(conn, command.target_id)
                    assert current is not None
                    payload = current["payload"]
                    if payload.get("control", {}).get("command_id") == child_id:
                        payload["control"]["status"] = status
                        self.save(conn, command.target_id, payload, current)
                    receipt = self.store.finish(
                        command.command_id,
                        "complete",
                        {**result, "control_id": child_id, "control_status": status},
                        connection=conn,
                    )
            return receipt
        except P3Error:
            raise HTTPException(503, detail={"code": "CASE_TASK_UNAVAILABLE"}) from None
        except PermissionError:
            raise HTTPException(403, detail={"code": "CASE_PERMISSION_REQUIRED"}) from None
        except ValueError:
            fail("CASE_VERSION_CONFLICT")

    @staticmethod
    def diagnose(conn: Connection[dict[str, Any]], task: TaskSnapshot) -> dict[str, Any]:
        checks = []
        user = conn.execute(
            "SELECT enabled FROM users WHERE id=%s AND tenant_id=%s",
            (scope(task).get("user_id"), scope(task).get("tenant_id")),
        ).fetchone()
        tenant = conn.execute(
            "SELECT enabled FROM tenants WHERE id=%s", (scope(task).get("tenant_id"),)
        ).fetchone()
        for title, row in (("所属企业", tenant), ("任务用户", user)):
            checks.append(
                {
                    "name": title,
                    "result": "已启用"
                    if row and row["enabled"]
                    else "已禁用"
                    if row
                    else "未找到对应记录",
                }
            )
        checks.extend(
            [
                {"name": "工作流", "result": task["workflow"]["state"] or "未取得状态"},
                {"name": "失败步骤", "result": task["stage"] or "原任务未记录具体步骤"},
                {
                    "name": "等待依赖",
                    "result": task["wait"].get("dependency_id") or "原任务未记录具体依赖",
                },
            ]
        )
        return {"at": now(), "checks": checks}
