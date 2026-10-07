"""Scoped admin commands over the existing Remember and Temporal pipelines."""

import asyncio
from typing import Annotated, Any, Literal, Self

from fastapi import FastAPI, Header, Response
from pydantic import Field, model_validator

from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    ErrorCode,
    Identifier,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.admin_execution import (
    authorize_admin_execution,
    bind_admin_context,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, now


class AdminMemoryCommand(ContractModel):
    action: Literal["create", "update", "delete", "archive", "activate", "recall"]
    tenant_id: Identifier
    user_id: Identifier
    memory_id: Identifier | None = None
    expected_version: int | None = Field(None, ge=1, strict=True)
    expected_object_revision: int | None = Field(None, ge=1, strict=True)
    text: str | None = Field(None, min_length=1, max_length=12000)
    category: Literal["observation", "event", "fact", "decision", "explicit_constraint"] = "fact"
    session_id: Identifier | None = None
    sources: Literal["auto", "both", "long_term", "working"] = "long_term"
    token_budget: int = Field(1800, ge=128, le=8192, strict=True)

    @model_validator(mode="after")
    def required_action_fields(self) -> Self:
        if self.action in {"create", "update", "recall"} and not (self.text or "").strip():
            raise ValueError("text required")
        if self.action in {"update", "delete", "archive", "activate"}:
            if (
                not self.memory_id
                or self.expected_version is None
                or self.expected_object_revision is None
            ):
                raise ValueError("memory, expected version and object revision required")
        elif (
            self.memory_id
            or self.expected_version is not None
            or self.expected_object_revision is not None
        ):
            raise ValueError("new command cannot target an existing memory version")
        if self.action == "recall" and self.sources in {"both", "working"} and not self.session_id:
            raise ValueError("working recall requires a session")
        return self


class AdminMemoryCommands:
    def __init__(self, runtime: Any, execution: Any) -> None:
        self.runtime, self.execution = runtime, execution
        self.identity, self.uow = runtime.foundation.identity, runtime.foundation.uow
        self.running: dict[str, asyncio.Task[Any]] = {}

    @staticmethod
    def key(ctx: TrustedContext, operation_id: str) -> str:
        return fingerprint(["admin_memory_command", ctx.principal.principal_id, operation_id])

    def load(
        self, ctx: TrustedContext, operation_id: str, tenant: str, user: str
    ) -> dict[str, Any]:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            row = tx.read("admin_memory_commands", self.key(ctx, operation_id))
            if row is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "admin command not found")
            command = row["command"]
            if (command["tenant_id"], command["user_id"]) != (tenant, user):
                raise FoundationError(ErrorCode.FORBIDDEN, "admin command target differs")
            authorize_admin_execution(self.identity, tx, ctx, tenant, user, command["action"])
            self.identity.revalidate(tx, self.bound(row, ctx.deadline_at))
            return dict(row)

    @staticmethod
    def bound(row: dict[str, Any], deadline: str | None = None) -> TrustedContext:
        ctx = TrustedContext.model_validate(row["context"])
        return ctx if deadline is None else ctx.model_copy(update={"deadline_at": deadline})

    def prepare(self, ctx: TrustedContext, command: AdminMemoryCommand) -> dict[str, Any]:
        key, signature = (
            self.key(ctx, ctx.operation_id),
            fingerprint(command.model_dump(mode="json")),
        )
        with self.uow.transaction() as tx:
            authorize_admin_execution(
                self.identity, tx, ctx, command.tenant_id, command.user_id, command.action
            )
            previous = tx.read("admin_memory_commands", key)
            if previous is not None:
                if previous["fingerprint"] != signature:
                    raise FoundationError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "admin operation input changed"
                    )
                self.identity.revalidate(tx, self.bound(previous, ctx.deadline_at))
                return dict(previous)
        bound = bind_admin_context(
            self.identity, ctx, command.tenant_id, command.user_id, command.action
        )
        with self.uow.transaction() as tx:
            # A concurrent same-ID submission must reuse the first durable context.
            previous = tx.read("admin_memory_commands", key)
            if previous is not None:
                if previous["fingerprint"] != signature:
                    raise FoundationError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "admin operation input changed"
                    )
                return dict(previous)
            self.identity.revalidate(tx, bound)
            if command.memory_id:
                pointer = tx.read("remember_current", command.memory_id)
                ref = RecordRef.model_validate(pointer) if pointer else None
                if ref is None:
                    raise FoundationError(ErrorCode.NOT_FOUND, "memory not found")
                home = bound.principal.home_scope
                if any(
                    getattr(ref.scope, name) != getattr(home, name)
                    for name in ("tenant_id", "user_id", "application_id", "agent_id")
                ):
                    raise FoundationError(ErrorCode.FORBIDDEN, "memory outside admin target")
                self.identity.authorize(tx, bound, Permission.READ, ref)
                item = tx.get(ref)
                if (
                    item is None
                    or item["ref"]["version"] != command.expected_version
                    or (
                        command.expected_object_revision is not None
                        and item["object_revision"] != command.expected_object_revision
                    )
                ):
                    raise FoundationError(ErrorCode.VERSION_CONFLICT, "memory version changed")
            row = {
                "key": key,
                "operation_id": ctx.operation_id,
                "operator_id": ctx.principal.principal_id,
                "command": command.model_dump(mode="json"),
                "fingerprint": signature,
                "context": bound.model_dump(mode="json"),
                "created_at": now(),
                "status": "pending",
                "job_id": None,
                "error_code": None,
            }
            tx.write("admin_memory_commands", key, row)
            return row

    @staticmethod
    def kind(row: dict[str, Any]) -> str:
        return {
            "create": "remember.save",
            "update": "remember.correct",
            "recall": "recall.execute",
            "archive": "remember.lifecycle",
            "activate": "remember.lifecycle",
            "delete": "remember.delete",
        }[row["command"]["action"]]

    def payload(self, row: dict[str, Any]) -> dict[str, Any]:
        command, ctx = row["command"], self.bound(row)
        source = {
            "kind": "text",
            "external_id": ctx.operation_id,
            "external_version": "1",
            "occurred_at": row["created_at"],
        }
        selection = {"user_id": command["user_id"], "session_id": command["session_id"]}
        action = command["action"]
        if action == "create":
            return {
                "source": source,
                "selection": selection,
                "content": {"kind": "text", "text": command["text"]},
                "importance_category": command["category"],
            }
        if action == "recall":
            return {
                "query": command["text"],
                "selection": selection,
                "sources": command["sources"],
                "token_budget": command["token_budget"],
            }
        if action == "update":
            return {
                "memory_id": command["memory_id"],
                "request": {
                    "expected_version": command["expected_version"],
                    "expected_object_revision": command["expected_object_revision"],
                    "content": command["text"],
                    "source": source,
                    "reason": "admin_memory_update",
                },
            }
        if action == "delete":
            return {
                "expected_revision": command["expected_object_revision"],
                "reason": "admin_memory_delete",
            }
        return {
            "expected_version": command["expected_version"],
            "expected_object_revision": command["expected_object_revision"],
            "target": "archived" if action == "archive" else "active",
            "reason": "admin_memory_" + action,
        }

    def update(self, row: dict[str, Any], **values: Any) -> dict[str, Any]:
        with self.uow.transaction() as tx:
            current = tx.read("admin_memory_commands", row["key"])
            # A late ambiguous delivery must never erase an observed terminal outcome.
            if current["status"] in {"succeeded", "failed"} and values.get("status") == "pending":
                return dict(current)
            updated = {**current, **values}
            tx.write("admin_memory_commands", row["key"], updated)
            return updated

    def dispatch(self, row: dict[str, Any]) -> None:
        if row["status"] != "pending" or row["job_id"]:
            return
        ctx, kind, payload = self.bound(row), self.kind(row), self.payload(row)
        try:
            # Resolve lost admissions before attempting the same stable command.
            if kind in {"remember.save", "remember.correct", "recall.execute"}:
                if self.execution is None:
                    raise FoundationError(
                        ErrorCode.DEPENDENCY_UNAVAILABLE, "Temporal execution required"
                    )
                found = self.execution.lookup_operation(ctx, ctx.operation_id, kind)
                if found.state == "found":
                    self.update(row, job_id=found.job_id, error_code=None)
                    return
                job = self.execution.accept(ctx, kind, payload)
                self.update(row, job_id=job.job_id, error_code=None)
            else:
                from aether_agent_memory.remember.contracts.models import (
                    DeleteRequest,
                    LifecycleRequest,
                )

                if kind == "remember.delete":
                    self.runtime.remember.delete(
                        ctx, row["command"]["memory_id"], DeleteRequest.model_validate(payload)
                    )
                else:
                    self.runtime.remember.lifecycle(
                        ctx, row["command"]["memory_id"], LifecycleRequest.model_validate(payload)
                    )
                self.update(row, status="succeeded", error_code=None)
        except FoundationError as exc:
            definite = {
                ErrorCode.INVALID_ARGUMENT,
                ErrorCode.VERSION_CONFLICT,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                ErrorCode.NOT_FOUND,
                ErrorCode.MEMORY_GONE,
                ErrorCode.FORBIDDEN,
            }
            self.update(
                row,
                status="failed" if exc.code in definite else "pending",
                error_code=str(exc.code),
            )
        except Exception:
            self.update(row, status="pending", error_code="EXECUTION_INTERRUPTED")

    @staticmethod
    def envelope(row: dict[str, Any], result: Any = None) -> dict[str, Any]:
        return {
            "operation_id": row["operation_id"],
            "status": row["status"],
            "job_id": row["job_id"],
            "error_code": row["error_code"],
            "result": result,
            "retry_allowed": False,
        }

    def read(
        self, ctx: TrustedContext, operation_id: str, tenant: str, user: str
    ) -> dict[str, Any]:
        row = self.load(ctx, operation_id, tenant, user)
        if row["status"] == "failed":
            return self.envelope(row)
        bound, kind = self.bound(row, ctx.deadline_at), self.kind(row)
        try:
            if kind in {"remember.save", "remember.correct", "recall.execute"}:
                if self.execution is None:
                    raise FoundationError(
                        ErrorCode.DEPENDENCY_UNAVAILABLE, "Temporal execution required"
                    )
                if not row["job_id"]:
                    found = self.execution.lookup_operation(bound, bound.operation_id, kind)
                    if found.state != "found":
                        return {**self.envelope(row), "retry_allowed": True}
                    row = self.update(row, job_id=found.job_id)
                result = self.execution.result(bound, row["job_id"])
            else:
                found = self.runtime.remember.mutations.lookup(bound, bound.operation_id, kind)
                if found.state != "committed":
                    return {**self.envelope(row), "retry_allowed": True}
                result = self.runtime.remember.mutations.result(
                    bound, bound.operation_id, kind
                ).response
            self.load(ctx, operation_id, tenant, user)
            return self.envelope(self.update(row, status="succeeded", error_code=None), result)
        except FoundationError as exc:
            if exc.code in {
                ErrorCode.FORBIDDEN,
                ErrorCode.UNAUTHENTICATED,
                ErrorCode.DEADLINE_EXCEEDED,
            }:
                raise
            if exc.code == ErrorCode.REQUEST_IN_PROGRESS:
                return self.envelope(row)
            if row["job_id"] and self.execution is not None:
                from aether_agent_memory.runtime.foundation.tasks import TERMINAL

                task = self.execution.status(bound, row["job_id"])
                if (
                    getattr(task, "effect_status", None) == "unknown"
                    or task.state == "attention_required"
                ):
                    return self.envelope(
                        {**row, "status": "pending", "error_code": str(task.error_code or exc.code)}
                    )
                if task.state in TERMINAL and task.state != "succeeded":
                    return self.envelope(
                        self.update(
                            row, status="failed", error_code=str(task.error_code or exc.code)
                        )
                    )
            # Never retain result bodies in the admin ledger; every read revalidates.
            return self.envelope(
                {
                    **row,
                    "status": "failed"
                    if exc.code
                    in {
                        ErrorCode.MEMORY_GONE,
                        ErrorCode.RESULT_INVALIDATED,
                        ErrorCode.VERSION_CONFLICT,
                    }
                    else "pending",
                    "error_code": str(exc.code),
                },
                None,
            )

    async def submit(self, ctx: TrustedContext, command: AdminMemoryCommand) -> dict[str, Any]:
        row = await asyncio.to_thread(self.prepare, ctx, command)
        if row["status"] == "pending" and row["key"] not in self.running:
            task = asyncio.create_task(asyncio.to_thread(self.dispatch, row))
            self.running[row["key"]] = task

            def finished(completed: asyncio.Task[Any]) -> None:
                self.running.pop(row["key"], None)
                if not completed.cancelled():
                    completed.exception()
                wakeup = getattr(self.execution, "wakeup", None)
                if wakeup is not None:
                    wakeup.set()

            task.add_done_callback(finished)
        # Admission and object I/O may continue after this response. The stored
        # request survives process loss and permits exact same-ID recovery.
        return self.envelope(row)


def attach(app: FastAPI, runtime: Any, execution: Any) -> None:
    service = AdminMemoryCommands(runtime, execution)
    app.state.admin_memory_commands = service
    dependency = app.state.trusted_dependency

    @app.post("/p3/admin/memory-commands")
    async def submit(
        command: AdminMemoryCommand,
        response: Response,
        x_operation_id: Annotated[Identifier, Header()],
        ctx: TrustedContext = dependency,
    ) -> dict[str, Any]:
        if x_operation_id != ctx.operation_id:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "operation header differs")
        response.headers["Cache-Control"] = "no-store"
        response.status_code = 202
        return await service.submit(ctx, command)

    @app.get("/p3/admin/memory-commands/{operation_id}")
    async def read(
        operation_id: Identifier,
        tenant_id: Identifier,
        user_id: Identifier,
        response: Response,
        ctx: TrustedContext = dependency,
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(service.read, ctx, operation_id, tenant_id, user_id)
