"""Scoped memory operations and recall schemes; original actor and request stay durable."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Self, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aether_platform.directory import Actor
from aether_platform.operations.console import Console, Identity
from aether_platform.operations.models import Command, page
from aether_platform.p3 import P3Client, P3Error, identifier

if TYPE_CHECKING:
    from aether_platform.operations.service import Operations

PERMISSIONS = {
    "memory_create": "aether:memory:create",
    "memory_update": "aether:memory:update",
    "memory_delete": "aether:memory:delete",
    "memory_archive": "aether:memory:update",
    "memory_activate": "aether:memory:update",
    "recall_execute": "aether:recall:execute",
    "recall_scheme_create": "aether:recall:manage",
    "recall_scheme_update": "aether:recall:manage",
    "recall_scheme_delete": "aether:recall:manage",
}


def fail(code: str, status: int = 409) -> NoReturn:
    raise HTTPException(status, detail={"code": code})


def require(actor: Actor, action: str | None = None) -> None:
    wanted = {"aether:ops:read", "aether:content:read"}
    if action:
        if action not in PERMISSIONS:
            fail("MEMORY_ACTION_INVALID", 422)
        wanted.update(("aether:memories:execute", PERMISSIONS[action]))
    permissions = set(getattr(actor, "permissions", ()))
    if actor.role not in {"platform_admin", "tenant_admin"} or (
        "*:*:*" not in permissions and not wanted.issubset(permissions)
    ):
        fail("MEMORY_PERMISSION_REQUIRED", 403)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class TextInput(Target):
    text: str = Field(min_length=1, max_length=12000)
    category: Literal["observation", "event", "fact", "decision", "explicit_constraint"] = "fact"
    session_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    expected_object_revision: int | None = Field(None, ge=1, strict=True)


class MutationInput(Target):
    expected_object_revision: int | None = Field(None, ge=1, strict=True)


class AbandonInput(Target):
    original_action: str


class RecallInput(Target):
    text: str | None = Field(None, min_length=1, max_length=12000)
    scheme_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    sources: Literal["long_term", "working", "both"] = "long_term"
    session_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    token_budget: int = Field(1800, ge=128, le=8000, strict=True)


class SchemeInput(Target):
    name: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=12000)
    sources: Literal["long_term", "working", "both"] = "long_term"
    session_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    token_budget: int = Field(1800, ge=128, le=8000, strict=True)

    @model_validator(mode="after")
    def working_session(self) -> Self:
        if self.sources in {"working", "both"} and not self.session_id:
            raise ValueError("session required for working memory")
        if not self.name.strip() or not self.query.strip():
            raise ValueError("blank scheme")
        return self


class MemoryAdmin:
    def __init__(self, operations: "Operations") -> None:
        self.ops = operations
        self.directory, self.store = operations.directory, operations.store
        self.console = Console(operations.config, operations.directory)

    def target(self, actor: Actor, user_id: str) -> dict[str, Any]:
        require(actor)
        return self.console.target(actor, user_id)

    def upstream(
        self,
        actor: Actor,
        token: str | Callable[[], str],
        method: str,
        operation_id: str,
        *,
        body: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = self.ops.config.get("p3", {})
        if not settings.get("base_url"):
            raise P3Error("P3_NOT_CONFIGURED")
        with P3Client(
            settings["base_url"],
            token,
            actor,
            admin_write=True,
            wait_seconds=0,
            trusted_http_host=settings.get("internal_http_host")
            or settings.get("trusted_http_host"),
        ) as client:
            return cast(
                dict[str, Any],
                client.call(
                    method,
                    "/p3/admin/memory-commands"
                    + ("/" + identifier(operation_id) if method == "GET" else ""),
                    body=body,
                    params=target if method == "GET" else None,
                    operation_id=operation_id if method == "POST" else None,
                ),
            )

    def read(
        self, actor: Actor, user_id: str, view: str, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        def read_target(target: dict[str, Any]) -> dict[str, Any]:
            with self.directory.connection() as conn:
                if view == "schemes":
                    where = (
                        "FROM ops_recall_schemes WHERE tenant_id=%s AND user_id=%s AND NOT deleted"
                    )
                    args = (target["tenant_id"], target["id"])
                    total = cast(
                        dict[str, Any],
                        conn.execute("SELECT count(*) AS n " + where, args).fetchone(),
                    )["n"]
                    rows = conn.execute(
                        "SELECT id,payload,version,updated_at "
                        + where
                        + " ORDER BY updated_at DESC,id LIMIT %s OFFSET %s",
                        (*args, limit, offset),
                    ).fetchall()
                    items = [
                        {
                            **r["payload"],
                            "id": r["id"],
                            "version": r["version"],
                            "updated_at": r["updated_at"],
                        }
                        for r in rows
                    ]
                else:
                    where = (
                        "FROM ops_memory_commands WHERE tenant_id=%s AND user_id=%s "
                        "AND action='recall_execute'"
                    )
                    args = (target["tenant_id"], target["id"])
                    total = cast(
                        dict[str, Any],
                        conn.execute("SELECT count(*) AS n " + where, args).fetchone(),
                    )["n"]
                    rows = conn.execute(
                        "SELECT "
                        "id,actor_id,tenant_id,user_id,action,status,result,created_at,updated_at "
                        + where
                        + " ORDER BY created_at DESC,id LIMIT %s OFFSET %s",
                        (*args, limit, offset),
                    ).fetchall()
                    items = [{**r, "command_id": r["id"], **r["result"]} for r in rows]
            return page(items, total)

        require(actor)
        if view not in {"schemes", "history"}:
            fail("MEMORY_QUERY_INVALID", 422)
        return self.console.content(actor, "recall_" + view, None, user_id, read_target)

    def validate(self, command: Command) -> dict[str, Any]:
        action = command.action
        model = (
            SchemeInput
            if action in {"recall_scheme_create", "recall_scheme_update"}
            else Target
            if action == "recall_scheme_delete"
            else TextInput
            if action in {"memory_create", "memory_update"}
            else RecallInput
            if action == "recall_execute"
            else MutationInput
        )
        try:
            parameters = model.model_validate(command.parameters).model_dump(exclude_none=True)
        except ValidationError:
            fail("MEMORY_PARAMETERS_INVALID", 422)
        if action in {
            "memory_update",
            "memory_delete",
            "memory_archive",
            "memory_activate",
            "recall_scheme_update",
            "recall_scheme_delete",
        }:
            if (
                not command.target_id
                or type(command.expected_version) is not int
                or command.expected_version < 1
            ):
                fail("MEMORY_VERSION_REQUIRED", 422)
        elif command.action == "recall_execute" and (
            parameters.get("scheme_id") or command.target_id
        ):
            if type(command.expected_version) is not int or command.expected_version < 1:
                fail("MEMORY_VERSION_REQUIRED", 422)
        elif command.expected_version not in (None, 0):
            fail("MEMORY_PARAMETERS_INVALID", 422)
        if action in {"memory_create", "recall_scheme_create"} and command.target_id:
            fail("MEMORY_PARAMETERS_INVALID", 422)
        if action in {
            "memory_update",
            "memory_delete",
            "memory_archive",
            "memory_activate",
        } and not parameters.get("expected_object_revision"):
            fail("MEMORY_VERSION_REQUIRED", 422)
        return parameters

    @staticmethod
    def scheme(
        conn: Connection[dict[str, Any]], target: dict[str, Any], sid: str
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM ops_recall_schemes WHERE id=%s AND tenant_id=%s AND "
            "user_id=%s AND NOT deleted FOR UPDATE",
            (sid, target["tenant_id"], target["id"]),
        ).fetchone()
        if not row:
            fail("MEMORY_SCHEME_NOT_FOUND", 404)
        return row

    def command(
        self, actor: Actor, token: str | Callable[[], str], command: Command
    ) -> dict[str, Any]:
        if command.action == "memory_abandon":
            return self.abandon(actor, token, command)
        require(actor, command.action)
        parameters = self.validate(command)
        target = self.target(actor, parameters["user_id"])
        try:
            with self.directory.connection() as conn:
                record, created = self.store.admit(actor, command, connection=conn)
                if command.action.startswith("recall_scheme_"):
                    if not created:
                        return {
                            "command_id": command.command_id,
                            "status": "succeeded",
                            "result": record["result"],
                        }
                    sid = command.target_id or str(uuid4())
                    if command.action != "recall_scheme_create":
                        old = self.scheme(conn, target, sid)
                        if old["version"] != command.expected_version:
                            fail("MEMORY_VERSION_CONFLICT")
                        version = old["version"] + 1
                        conn.execute(
                            "UPDATE ops_recall_schemes SET "
                            "payload=%s,version=%s,deleted=%s,updated_by=%s,"
                            "updated_at=now() WHERE id=%s",
                            (
                                Jsonb(
                                    parameters
                                    if command.action.endswith("update")
                                    else old["payload"]
                                ),
                                version,
                                command.action.endswith("delete"),
                                actor.id,
                                sid,
                            ),
                        )
                    else:
                        version = 1
                        conn.execute(
                            "INSERT INTO "
                            "ops_recall_schemes(id,tenant_id,user_id,payload,updated_by) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            (sid, target["tenant_id"], target["id"], Jsonb(parameters), actor.id),
                        )
                    result = {"id": sid, "version": version}
                    self.store.finish(command.command_id, "complete", result, connection=conn)
                    return {
                        "command_id": command.command_id,
                        "status": "succeeded",
                        "result": result,
                    }
                if created:
                    body = {
                        **parameters,
                        "tenant_id": target["tenant_id"],
                        "action": command.action.removeprefix("memory_"),
                    }
                    if command.action == "recall_execute":
                        body["action"] = "recall"
                        recall_sid = parameters.get("scheme_id") or command.target_id
                        if command.target_id and parameters.get("scheme_id") not in (
                            None,
                            command.target_id,
                        ):
                            fail("MEMORY_PARAMETERS_INVALID", 422)
                        if recall_sid:
                            scheme = self.scheme(conn, target, recall_sid)
                            if scheme["version"] != command.expected_version:
                                fail("MEMORY_VERSION_CONFLICT")
                            saved = scheme["payload"]
                            body = {
                                k: saved[k]
                                for k in ("sources", "session_id", "token_budget")
                                if k in saved
                            }
                            body.update(
                                action="recall",
                                text=saved["query"],
                                tenant_id=target["tenant_id"],
                                user_id=target["id"],
                            )
                        else:
                            body.pop("scheme_id", None)
                        if not body.get("text", "").strip() or (
                            body.get("sources") in {"working", "both"}
                            and not body.get("session_id")
                        ):
                            fail("MEMORY_PARAMETERS_INVALID", 422)
                    if command.action.startswith("memory_"):
                        if command.target_id:
                            body["memory_id"] = command.target_id
                        if command.expected_version:
                            body["expected_version"] = command.expected_version
                    conn.execute(
                        "INSERT INTO "
                        "ops_memory_commands(id,actor_id,tenant_id,user_id,action,request) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (
                            command.command_id,
                            actor.id,
                            target["tenant_id"],
                            target["id"],
                            command.action,
                            Jsonb(body),
                        ),
                    )
            return self.status(actor, token, command.command_id, initial=created)
        except PermissionError:
            fail("MEMORY_COMMAND_OWNER_REQUIRED", 403)
        except ValueError:
            fail("MEMORY_COMMAND_CONFLICT")

    def abandon(
        self, actor: Actor, token: str | Callable[[], str], command: Command
    ) -> dict[str, Any]:
        """Fence a never-admitted ID atomically; late original POSTs must then conflict."""
        try:
            params = AbandonInput.model_validate(command.parameters)
        except ValidationError:
            fail("MEMORY_PARAMETERS_INVALID", 422)
        require(actor, params.original_action)
        self.target(actor, params.user_id)
        if command.target_id or command.expected_version is not None:
            fail("MEMORY_PARAMETERS_INVALID", 422)
        with self.directory.connection() as conn:
            inserted = conn.execute(
                "INSERT INTO "
                "ops_commands(id,actor_id,tenant_id,resource,action,fingerprint,status) "
                "VALUES (%s,%s,%s,'memories',%s,'abandoned-before-admission','abandoned') "
                "ON CONFLICT(id) DO NOTHING RETURNING id",
                (command.command_id, actor.id, actor.tenant_id, params.original_action),
            ).fetchone()
            if inserted:
                return {
                    "id": command.command_id,
                    "command_id": command.command_id,
                    "status": "abandoned",
                }
            # A conflicting command exists when the insert did not return a row.
            record = cast(
                dict[str, Any],
                conn.execute(
                    "SELECT * FROM ops_commands WHERE id=%s", (command.command_id,)
                ).fetchone(),
            )
            if (
                record["actor_id"] != actor.id
                or record["action"] != params.original_action
                or record["resource"] != "memories"
            ):
                fail("MEMORY_COMMAND_CONFLICT")
            if record["status"] == "abandoned":
                return {
                    "id": command.command_id,
                    "command_id": command.command_id,
                    "status": "abandoned",
                }
        if params.original_action.startswith("recall_scheme_"):
            return {
                "id": command.command_id,
                "command_id": command.command_id,
                "status": "succeeded",
                "result": record["result"],
            }
        return self.status(actor, token, command.command_id)

    def status(
        self,
        actor: Actor,
        token: str | Callable[[], str],
        command_id: str,
        *,
        initial: bool = False,
    ) -> dict[str, Any]:
        require(actor)
        with self.directory.connection() as conn:
            row = conn.execute(
                "SELECT * FROM ops_memory_commands WHERE id=%s AND actor_id=%s",
                (command_id, actor.id),
            ).fetchone()
        if not row:
            fail("MEMORY_COMMAND_NOT_FOUND", 404)
        require(actor, row["action"])
        target = self.target(actor, row["user_id"])
        if target["tenant_id"] != row["tenant_id"]:
            fail("MEMORY_TARGET_CHANGED", 403)
        query = {"tenant_id": target["tenant_id"], "user_id": target["id"]}
        try:
            if initial:
                observed = self.upstream(actor, token, "POST", command_id, body=row["request"])
            else:
                try:
                    observed = self.upstream(actor, token, "GET", command_id, target=query)
                    if (
                        observed.get("status") == "pending"
                        and observed.get("retry_allowed") is True
                    ):
                        observed = self.upstream(
                            actor, token, "POST", command_id, body=row["request"]
                        )
                except P3Error as exc:
                    if exc.code not in {"NOT_FOUND", "HTTP_404"} or row["status"] in {
                        "succeeded",
                        "failed",
                    }:
                        raise
                    observed = self.upstream(actor, token, "POST", command_id, body=row["request"])
            status = observed.get("status")
            if status not in {"pending", "succeeded", "failed"}:
                raise P3Error("INVALID_RESPONSE")
            error_code = observed.get("error_code", observed.get("code"))
            compact: dict[str, Any] = {
                k: observed[k] for k in ("job_id", "operation_id") if k in observed
            }
            result = observed.get("result")
            if isinstance(result, dict) and result.get("recall_id"):
                compact["recall_id"] = result["recall_id"]
            if error_code:
                compact["error_code"] = error_code
            with self.directory.connection() as conn:
                conn.execute(
                    "UPDATE ops_memory_commands SET "
                    "status=%s,result=%s,updated_at=now() WHERE id=%s",
                    (status, Jsonb(compact), command_id),
                )
                self.store.finish(
                    command_id,
                    "complete" if status == "succeeded" else status,
                    compact,
                    connection=conn,
                )
            self.console.audit(
                actor, "memory_command", command_id, target["id"], target["tenant_id"], status
            )
            return {"id": command_id, "command_id": command_id, **observed, "code": error_code}
        except P3Error as exc:
            if exc.code in {"FORBIDDEN", "HTTP_403", "AUTHENTICATION_REQUIRED", "HTTP_401"}:
                fail("MEMORY_PERMISSION_REQUIRED", 403)
            if exc.code in {
                "VERSION_CONFLICT",
                "IDEMPOTENCY_CONFLICT",
                "INVALID_ARGUMENT",
                "MEMORY_GONE",
                "RESULT_INVALIDATED",
                "INVALID_IDENTIFIER",
            }:
                result = {"error_code": exc.code}
                with self.directory.connection() as conn:
                    conn.execute(
                        "UPDATE ops_memory_commands SET "
                        "status='failed',result=%s,updated_at=now() WHERE id=%s",
                        (Jsonb(result), command_id),
                    )
                    self.store.finish(command_id, "failed", result, connection=conn)
                return {
                    "id": command_id,
                    "command_id": command_id,
                    "status": "failed",
                    "code": exc.code,
                }
            return {
                "id": command_id,
                "command_id": command_id,
                "status": "pending",
                "code": "MEMORY_RESULT_UNCONFIRMED",
            }


def install_memory_admin(app: FastAPI, operations: "Operations", identity: Identity) -> None:
    from fastapi import Query, Request

    @app.get("/platform-ops/v1/console/recalls", response_model=None)
    def recalls(
        request: Request,
        user_id: str = Query(..., pattern=r"^[A-Za-z0-9_-]{1,128}$"),
        q: Literal["schemes", "history", "records"] = "schemes",
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        cursor: str | None = Query(None, max_length=4096),
    ) -> dict[str, Any]:
        actor, token = identity(request)
        service = MemoryAdmin(operations)
        if q == "records":
            require(actor)
            return service.console.content(
                actor,
                "recall_records",
                None,
                user_id,
                lambda target: service.console.business(
                    actor,
                    token,
                    "recalls",
                    target,
                    limit=limit,
                    cursor=cursor,
                ),
            )
        return service.read(actor, user_id, q, limit, offset)
