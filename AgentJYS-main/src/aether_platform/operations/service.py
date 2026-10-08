"""Operational queries never read conversation bodies or mutate P3 storage directly."""

from collections.abc import Callable
from typing import Any, cast

from fastapi import HTTPException

from aether_platform.directory import Actor, Directory
from aether_platform.operations.models import Command, now, page, public_request
from aether_platform.operations.store import OpsStore
from aether_platform.p3 import P3Client, P3Error, identifier

RECORD_FIELDS = {
    "quotas": {"daily_requests", "concurrent_turns", "observed_token_budget"},
    "resources": {"name", "kind", "environment", "owner", "purpose", "dependency_ids", "status"},
    "support": {"summary", "request_id", "severity", "state", "owner", "resolution"},
    "configuration": {"name", "value", "description"},
    "rules": {"metric", "threshold", "enabled", "description"},
}
CONTROL_STATUSES: dict[str | None, str] = {
    "completed": "complete",
    "failed": "failed",
    "unknown": "unknown",
}


class Operations:
    def __init__(self, config: dict[str, Any], directory: Directory) -> None:
        self.config, self.directory = config, directory
        self.store = OpsStore(directory)
        self.store.migrate()
        from aether_platform.operations.collector import Collector

        self.collector = Collector(config, self.store)

    def start(self) -> None:
        self.collector.start()

    def stop(self) -> None:
        self.collector.stop()

    def p3_call(
        self,
        actor: Actor,
        token: str | Callable[[], str],
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = self.config.get("p3", {})
        if not settings.get("base_url"):
            raise P3Error("P3_NOT_CONFIGURED")
        with P3Client(
            settings["base_url"],
            token,
            actor,
            operator=True,
            wait_seconds=0,
            trusted_http_host=settings.get("internal_http_host")
            or settings.get("trusted_http_host"),
        ) as client:
            return cast(dict[str, Any], client.call(method, path, body=body, params=params))

    def requests(self, actor: Actor, limit: int, offset: int) -> dict[str, Any]:
        with self.directory.connection() as conn:
            scope = self.store.scope(actor)
            total = cast(
                dict[str, Any],
                conn.execute(
                    "SELECT count(*) AS n FROM chat_turns t JOIN conversations c ON "
                    "c.id=t.conversation_id WHERE (%s OR c.tenant_id=%s)",
                    scope,
                ).fetchone(),
            )["n"]
            rows = conn.execute(
                "SELECT t.id,t.conversation_id,c.user_id,c.tenant_id,t.status,t.phase,t.created_at,"
                "t.started_at,t.first_token_at,t.attempt,(t.error IS NOT NULL) AS error,"
                "EXTRACT(EPOCH FROM (t.first_token_at-t.started_at))*1000 AS first_token_ms,"
                "jsonb_build_object('status',t.memory_evidence-"
                ">>'status','job_id',t.memory_evidence->>'job_id',"
                "'operation_id',t.memory_evidence->>'operation_id','memory_id',t.memory_evidence-"
                ">>'memory_id',"
                "'recall_id',t.memory_evidence->>'recall_id') AS memory_evidence "
                "FROM chat_turns t JOIN conversations c ON c.id=t.conversation_id "
                "WHERE (%s OR c.tenant_id=%s) ORDER BY t.created_at DESC,t.id LIMIT %s OFFSET %s",
                (*scope, limit, offset),
            ).fetchall()
        return page([public_request(row) for row in rows], total)

    def usage(self, actor: Actor) -> dict[str, Any]:
        with self.directory.connection() as conn:
            rows = conn.execute(
                "SELECT c.tenant_id,count(*) AS requests,"
                "count(*) FILTER(WHERE t.status='complete') AS complete,"
                "count(*) FILTER(WHERE t.status='failed') AS failed,"
                "count(*) FILTER(WHERE t.status='pending') AS pending,"
                "count(*) FILTER(WHERE t.memory_evidence->>'status'='saved') AS saved,"
                "percentile_cont(0.95) WITHIN GROUP(ORDER BY EXTRACT(EPOCH FROM "
                "(t.first_token_at-t.started_at))*1000) AS first_token_p95_ms "
                "FROM chat_turns t JOIN conversations c ON c.id=t.conversation_id "
                "WHERE (%s OR c.tenant_id=%s) AND t.created_at >= now()-interval '24 hours' GROUP"
                " BY c.tenant_id",
                self.store.scope(actor),
            ).fetchall()
            model_rows = conn.execute(
                "SELECT c.tenant_id,m.model_name,count(*) AS measured_attempts,"
                "sum((m.usage->>'prompt_tokens')::bigint) AS prompt_tokens,"
                "sum((m.usage->>'completion_tokens')::bigint) AS completion_tokens,"
                "sum((m.usage->>'total_tokens')::bigint) AS total_tokens "
                "FROM chat_turns t JOIN conversations c ON c.id=t.conversation_id "
                "JOIN chat_model_usage m ON m.turn_id=t.id WHERE (%s OR c.tenant_id=%s) "
                "AND m.observed_at>=now()-interval '24 hours' GROUP BY c.tenant_id,m.model_name",
                self.store.scope(actor),
            ).fetchall()
        from aether_platform.operations.metering import estimate_cost

        for row in model_rows:
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                row[field] = int(row[field])
            rates = (
                self.config.get("operations", {}).get("model_pricing", {}).get(row["model_name"])
            )
            row["estimated_cost"] = estimate_cost(row, rates)
            row["currency"] = rates.get("currency") if rates else None
        return page(
            rows,
            window="24h",
            model_usage=model_rows,
            metering_status="partial" if model_rows else "no_provider_usage_yet",
            metering_scope="Agent provider-reported attempts including retries; "
            "unmetered requests and P3 internal calls excluded",
        )

    def read(
        self,
        actor: Actor,
        token: str | Callable[[], str],
        resource: str,
        limit: int = 50,
        offset: int = 0,
        *,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        from aether_platform.operations.presentation import attach_names

        result = self._read(actor, token, resource, limit, offset, cursor=cursor)
        if resource == "tasks":
            return result  # Task metadata has no directory identities to resolve.
        return attach_names(result, resource, actor, self.directory)

    def _read(
        self,
        actor: Actor,
        token: str | Callable[[], str],
        resource: str,
        limit: int = 50,
        offset: int = 0,
        *,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if resource == "requests":
            return self.requests(actor, limit, offset)
        if resource == "memories":
            result = self.requests(actor, limit, offset)
            result["source"] = "chat_memory_receipts"
            result["scope_note"] = "会话记忆回执；不等同完整记忆目录或索引就绪证明"
            return result
        if resource == "usage":
            return self.usage(actor)
        if resource in {"audit", "commands"}:
            return self.store.commands(actor, limit, offset)
        if resource in {"resources", "support", "backups", "configuration", "rules", "quotas"}:
            result = self.store.records(actor, resource, limit, offset)
            if resource == "quotas":
                with self.directory.connection() as conn:
                    result["tenant_choices"] = conn.execute(
                        "SELECT id,name FROM tenants ORDER BY name"
                    ).fetchall()
            if resource == "configuration":
                result["release"] = {
                    key: self.config.get("release", {}).get(key)
                    for key in ("commit", "image_digest", "deployed_at", "environment")
                }
                result["scope_note"] = (
                    "运维配置记录与 P3 配置快照版本；激活快照不等同模型、存储等运行参数热更新"
                )
                try:
                    result["active_snapshot"] = self.p3_call(
                        actor, token, "GET", "/p3/configuration"
                    )
                    result["current_status"] = "available"
                except P3Error as exc:
                    result.update(active_snapshot=None, current_status="unavailable", code=exc.code)
            if resource == "backups":
                result["executor_status"] = (
                    "configured"
                    if self.config.get("operations", {}).get("backup_directory")
                    else "not_configured"
                )
            return result
        if resource == "incidents":
            with self.directory.connection() as conn:
                deliveries = conn.execute(
                    "SELECT d.alert_id,d.channel,d.state,d.code,d.created_at FROM ops_deliveries "
                    "d JOIN ops_alerts a ON a.id=d.alert_id WHERE (%s OR a.tenant_id=%s) ORDER BY"
                    " d.id DESC LIMIT 100",
                    self.store.scope(actor),
                ).fetchall()
                latest = cast(
                    dict[str, Any],
                    conn.execute(
                        "SELECT max(observed_at) AS observed_at FROM ops_samples WHERE (%s OR "
                        "tenant_id=%s)",
                        self.store.scope(actor),
                    ).fetchone(),
                )["observed_at"]
            return page(
                self.store.alerts(actor),
                deliveries=deliveries,
                last_sample_at=latest,
                notification_status="configured"
                if self.config.get("operations", {}).get("notification_url")
                else "not_configured",
            )
        if resource == "overview":
            from aether_platform.operations.performance import performance

            result = {
                "observed_at": now(),
                "usage": self.usage(actor),
                "performance": performance(self.directory, actor),
                "alerts": self.store.alerts(actor),
                "status": "ok",
            }
            if actor.role == "platform_admin":
                try:
                    result["p3"] = self.p3_call(actor, token, "GET", "/p3/health")
                except P3Error as exc:
                    result.update(status="degraded", p3={"status": "unknown", "code": exc.code})
            return result
        if resource == "tasks":
            try:
                if offset:
                    raise HTTPException(409, "任务列表使用 next_cursor 翻页")
                result = self.p3_call(
                    actor,
                    token,
                    "GET",
                    "/p3/tasks",
                    params={"limit": limit, **({"cursor": cursor} if cursor else {})},
                )
                rows = result.get("items", result.get("tasks", []))
                # Business task payloads may contain content; show metadata only.
                fields = {
                    "task_id",
                    "job_id",
                    "id",
                    "kind",
                    "flow",
                    "owner_flow",
                    "effect_status",
                    "error_code",
                    "max_attempts",
                    "deadline_at",
                    "state",
                    "revision",
                    "created_at",
                    "updated_at",
                    "attempt",
                    "next_run_at",
                }
                return page(
                    [{k: v for k, v in row.items() if k in fields} for row in rows],
                    next_cursor=result.get("next_cursor"),
                )
            except P3Error as exc:
                return page([], status="unavailable", code=exc.code)
        raise HTTPException(404, "没有此资源")

    def command_status(
        self, actor: Actor, token: str | Callable[[], str], command_id: str
    ) -> dict[str, Any]:
        record = self.store.command_record(actor, command_id)
        if not record:
            raise HTTPException(404, "没有此操作")
        if record["resource"] == "memories":
            from aether_platform.operations.memory_admin import MemoryAdmin, require

            require(actor, record["action"])
            if record["status"] == "abandoned":
                if record["actor_id"] != actor.id:
                    raise HTTPException(404, "没有此操作")
                return page([{**record, "command_id": command_id}], 1)
            if record["action"].startswith("recall_scheme_"):
                if record["actor_id"] != actor.id:
                    raise HTTPException(404, "没有此操作")
                return page([{**record, "command_id": command_id, "status": "succeeded"}], 1)
            return page([MemoryAdmin(self).status(actor, token, command_id)], 1)
        if (
            record["resource"] == "configuration"
            and record["action"] == "activate"
            and record["status"] in {"submitted", "unknown"}
            and record["result"].get("desired_version")
        ):
            try:
                current = self.p3_call(actor, token, "GET", "/p3/configuration")
                if current and (current.get("version"), current.get("config_hash")) == (
                    record["result"]["desired_version"],
                    record["result"]["desired_hash"],
                ):
                    record = self.store.finish(
                        command_id,
                        "complete",
                        {k: current.get(k) for k in ("version", "config_hash", "activated_at")},
                    )
            except P3Error:
                pass
        if record["resource"] == "backups" and record["status"] in {"submitted", "unknown"}:
            from aether_platform.operations.backups import BackupExecutor

            try:
                result = BackupExecutor(self.config, self.directory).completed(
                    record["action"], record["target_id"], command_id
                )
                if result:
                    record = self.finish_backup(actor, record, result)
            except (ValueError, OSError):
                pass
        if (
            record["resource"] == "tasks"
            and record["action"] in {"cancel", "reconcile"}
            and record["status"] in {"accepted", "unknown", "submitted"}
        ):
            try:
                observed = self.p3_call(
                    actor, token, "GET", "/p3/controls/" + identifier(command_id)
                )
                status = CONTROL_STATUSES.get(observed.get("state"), "accepted")
                result = {
                    k: v
                    for k, v in observed.items()
                    if k in {"operation_id", "state", "phase", "task_ids", "outcome"}
                }
                record = self.store.finish(command_id, status, result)
            except P3Error as exc:
                if exc.code in {"NOT_FOUND", "HTTP_404"}:
                    record = {**record, "lookup_status": "not_found"}
        return page([record], 1)

    def resume_task_control(
        self, actor: Actor, token: str | Callable[[], str], command: Command
    ) -> dict[str, Any]:
        """Resume only an exact original control after an authoritative missing lookup."""
        from aether_platform.operations.task_cases import require

        require(actor, "aether:tasks:execute")
        self._validate(command)
        if command.resource != "tasks" or command.action not in {"cancel", "reconcile"}:
            raise HTTPException(409, detail={"code": "CASE_ACTION_INVALID"})
        record, _ = self.store.admit(actor, command)
        if record["status"] not in {"submitted", "unknown"}:
            return record
        try:
            # Recheck absence here, not merely at the caller. Repeated POST uses
            # ControlAdmission.existing's durable operation-id/signature fence.
            observed = self.p3_call(
                actor, token, "GET", "/p3/controls/" + identifier(command.command_id)
            )
            return self.store.finish(
                command.command_id,
                CONTROL_STATUSES.get(observed.get("state"), "accepted"),
                {
                    k: v
                    for k, v in observed.items()
                    if k in {"operation_id", "state", "phase", "revision", "task_ids", "outcome"}
                },
            )
        except P3Error as exc:
            if exc.code not in {"NOT_FOUND", "HTTP_404"}:
                return record
        try:
            result = self.p3_call(
                actor,
                token,
                "POST",
                "/p3/tasks/" + identifier(cast(str, command.target_id)) + "/control",
                body={
                    "operation_id": command.command_id,
                    "action": command.action,
                    "expected_revision": command.expected_version,
                    "reason": command.parameters["reason"],
                },
            )
            safe = {
                k: v
                for k, v in result.items()
                if k in {"operation_id", "state", "phase", "revision", "task_ids", "outcome"}
            }
            return self.store.finish(command.command_id, "accepted", safe)
        except P3Error as exc:
            unknown = exc.code in {
                "CONNECTION_UNCONFIRMED",
                "REQUEST_IN_PROGRESS",
                "INVALID_RESPONSE",
            }
            return self.store.finish(
                command.command_id, "unknown" if unknown else "failed", {"code": exc.code}
            )

    def finish_backup(
        self, actor: Actor, record: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        with self.directory.connection() as conn:
            locked = cast(
                dict[str, Any],
                conn.execute(
                    "SELECT * FROM ops_commands WHERE id=%s FOR UPDATE", (record["id"],)
                ).fetchone(),
            )
            if locked["status"] == "complete":
                return locked
            self.store.save_record(
                actor,
                "backups",
                record["target_id"] + ("-drill" if record["action"] == "restore_drill" else ""),
                result,
                None,
                connection=conn,
            )
            return self.store.finish(record["id"], "complete", result, connection=conn)

    @staticmethod
    def _validate(command: Command) -> None:
        if command.resource == "tasks" and command.action in {"cancel", "reconcile", "status"}:
            if not command.target_id:
                raise ValueError("Task identifier required")
            if command.action != "status" and (
                not isinstance(command.expected_version, int) or command.expected_version < 1
            ):
                raise ValueError("Expected task revision required")
            if command.action != "status" and not str(command.parameters.get("reason", "")).strip():
                raise ValueError("Reason required")
            if set(command.parameters) - {"reason"}:
                raise ValueError("Unexpected control parameter")
        elif command.resource in RECORD_FIELDS and command.action == "save":
            if (
                not command.target_id
                or not command.parameters
                or set(command.parameters) - RECORD_FIELDS[command.resource]
            ):
                raise ValueError("Invalid record fields")
            if len(str(command.parameters)) > 16000:
                raise ValueError("Record too large")
            if command.resource == "quotas" and any(
                type(v) is not int or not 1 <= v <= 1000000000 for v in command.parameters.values()
            ):
                raise ValueError("Quota limits must be positive integers")
            if command.resource == "rules":
                if command.parameters.get("metric") not in {
                    "request_failures_15m",
                    "oldest_pending_seconds",
                }:
                    raise ValueError("Unsupported metric")
                threshold = command.parameters.get("threshold")
                if (
                    isinstance(threshold, bool)
                    or not isinstance(threshold, (int, float))
                    or not 0 <= threshold <= 86400
                ):
                    raise ValueError("Invalid threshold")
                if "enabled" in command.parameters and not isinstance(
                    command.parameters["enabled"], bool
                ):
                    raise ValueError("Invalid enabled flag")
        elif command.resource == "incidents" and command.action in {"acknowledge", "silence"}:
            if not command.target_id or set(command.parameters) - {"note", "minutes"}:
                raise ValueError("Invalid alert action")
            if not 1 <= int(command.parameters.get("minutes", 30)) <= 1440:
                raise ValueError("Invalid silence duration")
        elif command.resource == "configuration" and command.action == "activate":
            if not isinstance(command.parameters.get("snapshot"), dict):
                raise ValueError("Configuration snapshot must be an object")
            if set(command.parameters) != {"snapshot"} or (
                command.expected_version is not None
                and (
                    not isinstance(command.expected_version, str)
                    or not 1 <= len(command.expected_version) <= 128
                )
            ):
                raise ValueError("Configuration snapshot and current version required")
        elif command.resource == "backups" and command.action in {"create", "restore_drill"}:
            if command.parameters or not command.target_id:
                raise ValueError("Backup identifier required; arbitrary targets prohibited")
        else:
            raise ValueError("Unsupported operation")

    def local_command(self, actor: Actor, command: Command) -> dict[str, Any]:
        # Admission, side effect and completion commit together. A process exit cannot
        # leave a committed local change with an uncommitted command receipt.
        with self.directory.connection() as conn:
            record, created = self.store.admit(actor, command, connection=conn)
            if not created:
                return record
            if command.resource in RECORD_FIELDS and command.action == "save":
                if (
                    command.resource == "quotas"
                    and not conn.execute(
                        "SELECT 1 FROM tenants WHERE id=%s", (command.target_id,)
                    ).fetchone()
                ):
                    raise ValueError("Unknown tenant")
                result = self.store.save_record(
                    actor,
                    command.resource,
                    cast(str, command.target_id),
                    command.parameters,
                    command.expected_version,
                    connection=conn,
                )
                result = {
                    "id": result["id"],
                    "version": result["version"],
                    "scope": "management_record",
                }
            elif command.resource == "incidents":
                result = self.store.update_alert(
                    actor,
                    cast(str, command.target_id),
                    command.action,
                    str(command.parameters.get("note", ""))[:1000],
                    silence_minutes=int(command.parameters.get("minutes", 30)),
                    connection=conn,
                )
                result = {"id": result["id"], "state": result["state"]}
            return self.store.finish(command.command_id, "complete", result, connection=conn)

    def command(
        self, actor: Actor, token: str | Callable[[], str], command: Command
    ) -> dict[str, Any]:
        if command.resource == "memories":
            from aether_platform.operations.memory_admin import MemoryAdmin

            return MemoryAdmin(self).command(actor, token, command)
        if command.resource == "support" and command.action.startswith("case_"):
            from aether_platform.operations.task_cases import TaskCases

            return TaskCases(self).command(actor, token, command)
        admitted = False
        recovery_evidence: dict[str, Any] = {}
        try:
            self._validate(command)
            if (command.resource in RECORD_FIELDS and command.action == "save") or (
                command.resource == "incidents"
            ):
                return self.local_command(actor, command)
            record, created = self.store.admit(actor, command)
            if not created:
                return record
            admitted = True
            if command.resource == "tasks":
                if command.action == "status":
                    result = self.p3_call(
                        actor,
                        token,
                        "GET",
                        "/p3/controls/" + identifier(cast(str, command.target_id)),
                    )
                    status = "complete"
                else:
                    result = self.p3_call(
                        actor,
                        token,
                        "POST",
                        "/p3/tasks/" + identifier(cast(str, command.target_id)) + "/control",
                        body={
                            "operation_id": command.command_id,
                            "action": command.action,
                            "expected_revision": command.expected_version,
                            "reason": command.parameters["reason"],
                        },
                    )
                    status = "accepted"
                result = {
                    k: v
                    for k, v in result.items()
                    if k in {"operation_id", "state", "phase", "revision", "task_ids", "outcome"}
                }
            elif command.resource == "configuration":
                snapshot = command.parameters["snapshot"]
                recovery_evidence = {
                    "desired_version": snapshot.get("version"),
                    "desired_hash": snapshot.get("config_hash"),
                }
                self.store.finish(command.command_id, "submitted", recovery_evidence)
                result = self.p3_call(
                    actor,
                    token,
                    "PUT",
                    "/p3/configuration",
                    body={
                        "snapshot": command.parameters["snapshot"],
                        "expected_version": command.expected_version,
                    },
                )
                result, status = (
                    {k: result.get(k) for k in ("version", "config_hash", "activated_at")},
                    "complete",
                )
            elif command.resource == "backups":
                from aether_platform.operations.backups import BackupExecutor

                executor = BackupExecutor(self.config, self.directory)
                result = executor.run(
                    command.action, cast(str, command.target_id), command_id=command.command_id
                )
                return self.finish_backup(actor, record, result)
            return self.store.finish(command.command_id, status, result)
        except P3Error as exc:
            status = (
                "unknown"
                if exc.code in {"CONNECTION_UNCONFIRMED", "REQUEST_IN_PROGRESS", "INVALID_RESPONSE"}
                else "failed"
            )
            return self.store.finish(
                command.command_id, status, {"code": exc.code, **recovery_evidence}
            )
        except PermissionError:
            if admitted:
                self.store.finish(command.command_id, "failed", {"code": "FORBIDDEN"})
            raise HTTPException(403, "没有此对象的权限") from None
        except ValueError:
            if admitted:
                self.store.finish(
                    command.command_id, "failed", {"code": "INVALID_ARGUMENT_OR_VERSION"}
                )
            raise HTTPException(409, "参数无效、版本冲突或操作编号已使用") from None
        except (RuntimeError, OSError):
            if admitted:
                return self.store.finish(
                    command.command_id, "unknown", {"code": "EXECUTOR_FAILED_CHECK_ORIGINAL"}
                )
            raise HTTPException(503, "运维执行器暂不可用") from None
