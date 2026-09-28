"""Authenticated HTTP composition over the same P3 services and worker loop."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from aether_agent_memory.recall.contracts.models import ContextPack, RecallRecord, RecallRequest
from aether_agent_memory.remember.contracts.models import RememberReceipt, RememberRequest
from aether_agent_memory.runtime.contracts.foundation import (
    BackupManifest,
    ConfigurationSnapshot,
    IncidentRecord,
    RuntimeHealthSnapshot,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    ErrorResponse,
    Identifier,
    OperationRecord,
    RecoveryRequest,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, now

from .host import ThreeFlows


class ConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: ConfigurationSnapshot
    expected_version: str | None = None


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backup_id: Identifier


class RestoreRequest(BackupRequest):
    restore_id: Identifier


def create_app(
    runtime: ThreeFlows,
    *,
    run_worker: bool = True,
    maintenance_credential: Callable[[], str] | None = None,
    supervisor: Any = None,
    close: Any = None,
) -> FastAPI:
    """Caller owns runtime lifetime. Credentials are supplied at use, never logged."""
    stopped = asyncio.Event()
    state: dict[str, Any] = {"worker": "disabled" if not run_worker else "starting"}
    if supervisor is not None:
        state = supervisor.state
        run_worker = False

    async def worker() -> None:
        while not stopped.is_set():
            try:
                context = None
                maintenance_unavailable = False
                if maintenance_credential:
                    try:
                        context = runtime.foundation.identity.context(
                            maintenance_credential(), timeout_seconds=60
                        )
                    except FoundationError:
                        maintenance_unavailable = True
                await runtime.tick(periodic=True, maintenance_context=context)
                state["worker"] = "degraded" if maintenance_unavailable else "running"
            except Exception:
                # Preserve pending work and report the failure; never rewrite its
                # effect status or dump provider messages/credentials to logs.
                state["worker"] = "degraded"
            with suppress(TimeoutError):
                await asyncio.wait_for(stopped.wait(), timeout=0.25)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stopped.clear()
        task = None
        try:
            if supervisor is not None:
                await supervisor.start()
            task = asyncio.create_task(worker()) if run_worker else None
            yield
        finally:
            stopped.set()
            if task is not None:
                try:
                    await asyncio.wait_for(task, timeout=5)
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            state["worker"] = "stopped"
            if supervisor is not None:
                await supervisor.stop()
            if close is not None:
                await close()

    app = FastAPI(title="P3 runtime", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.supervisor = supervisor

    def context(
        request: Request,
        authorization: str | None = Header(default=None),
        x_operation_id: str | None = Header(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$"),
    ) -> TrustedContext:
        if not authorization or not authorization.startswith("Bearer "):
            raise FoundationError(ErrorCode.UNAUTHENTICATED, "bearer credential required")
        trusted = runtime.foundation.identity.context(
            authorization[7:], operation_id=x_operation_id
        )
        request.state.context = trusted
        return trusted

    trusted_dependency = Depends(context)

    @app.exception_handler(FoundationError)
    async def foundation_error(request: Request, error: FoundationError) -> JSONResponse:
        trusted = getattr(request.state, "context", None)
        status = {
            ErrorCode.UNAUTHENTICATED: 401,
            ErrorCode.FORBIDDEN: 403,
            ErrorCode.NOT_FOUND: 404,
            ErrorCode.VERSION_CONFLICT: 409,
            ErrorCode.IDEMPOTENCY_CONFLICT: 409,
            ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
            ErrorCode.DEADLINE_EXCEEDED: 504,
            ErrorCode.RESULT_INVALIDATED: 410,
            ErrorCode.MEMORY_GONE: 410,
        }.get(error.code, 400)
        result = ErrorResponse(
            code=error.code,
            message=error.code.value.lower(),
            retryable=status in {503, 504},
            request_id=trusted.request_id if trusted else secrets.token_hex(16),
            operation_id=trusted.operation_id if trusted else None,
        )
        return JSONResponse(status_code=status, content=result.model_dump(mode="json"))

    @app.get("/p3/live")
    def live() -> dict[str, str]:
        return {"liveness": "alive", "checked_at": now()}

    @app.exception_handler(ValueError)
    async def invalid_request(request: Request, error: ValueError) -> JSONResponse:
        return await foundation_error(
            request, FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid request")
        )

    @app.exception_handler(FileExistsError)
    async def existing_artifact(request: Request, error: FileExistsError) -> JSONResponse:
        return await foundation_error(
            request, FoundationError(ErrorCode.IDEMPOTENCY_CONFLICT, "artifact already exists")
        )

    @app.get("/p3/runtime")
    def runtime_state(ctx: TrustedContext = trusted_dependency) -> dict[str, Any]:
        return {
            **runtime.health.runtime(ctx),
            "http_worker": state["worker"],
            "worker_lanes": state.get("lanes", {}),
            "identity_configuration": state.get("identity", "static"),
        }

    @app.get("/p3/health", response_model=RuntimeHealthSnapshot)
    async def health(ctx: TrustedContext = trusted_dependency) -> RuntimeHealthSnapshot:
        await runtime.health.report(ctx)
        return runtime.foundation.monitoring.health(ctx)

    @app.get("/p3/ready")
    async def ready(ctx: TrustedContext = trusted_dependency) -> JSONResponse:
        snapshot = await health(ctx)
        return JSONResponse(
            status_code=200
            if snapshot.readiness == "ready"
            and (supervisor is None or state["worker"] == "running")
            else 503,
            content=snapshot.model_dump(mode="json"),
        )

    @app.get("/p3/logs/{trace_id}")
    def logs(
        trace_id: str, after: int = 0, limit: int = 100, ctx: TrustedContext = trusted_dependency
    ) -> dict[str, Any]:
        return runtime.foundation.monitoring.logs(ctx, trace_id, after=after, limit=limit)

    @app.get("/p3/tasks/{task_id}", response_model=TaskRecord)
    def task(task_id: str, ctx: TrustedContext = trusted_dependency) -> TaskRecord:
        return runtime.foundation.diagnostics.task(ctx, task_id)

    @app.get("/p3/tasks/{task_id}/progress")
    def progress(task_id: str, ctx: TrustedContext = trusted_dependency) -> dict[str, object]:
        return runtime.foundation.tasks.progress.read(ctx, task_id)

    @app.post("/p3/recovery", response_model=OperationRecord)
    def recovery(
        request: RecoveryRequest, ctx: TrustedContext = trusted_dependency
    ) -> OperationRecord:
        with runtime.foundation.uow.transaction() as tx:
            return runtime.foundation.tasks.request_recovery(tx, ctx, request)

    @app.get("/p3/incidents", response_model=tuple[IncidentRecord, ...])
    def incidents(ctx: TrustedContext = trusted_dependency) -> tuple[IncidentRecord, ...]:
        return runtime.foundation.dispositions.incidents(ctx)

    @app.post("/p3/maintenance/cycle")
    async def cycle(ctx: TrustedContext = trusted_dependency) -> dict[str, int]:
        runtime.foundation.diagnostics.authorize(ctx)
        return await runtime.foundation.dispositions.cycle(ctx)

    @app.put("/p3/configuration", response_model=ConfigurationSnapshot)
    def configure(
        request: ConfigurationRequest, ctx: TrustedContext = trusted_dependency
    ) -> ConfigurationSnapshot:
        result = runtime.foundation.lifecycle.activate(
            ctx, request.snapshot, expected_version=request.expected_version
        )
        runtime.foundation.monitoring.config_version = result.version
        return result

    @app.post("/p3/backups", response_model=BackupManifest)
    def backup(request: BackupRequest, ctx: TrustedContext = trusted_dependency) -> BackupManifest:
        return runtime.foundation.lifecycle.backup(ctx, request.backup_id)

    @app.post("/p3/restore-drills", response_model=BackupManifest)
    def restore(
        request: RestoreRequest, ctx: TrustedContext = trusted_dependency
    ) -> BackupManifest:
        return runtime.foundation.lifecycle.restore(ctx, request.backup_id, request.restore_id)

    @app.post("/p3/remember", response_model=RememberReceipt)
    async def remember(
        request: RememberRequest, ctx: TrustedContext = trusted_dependency
    ) -> RememberReceipt:
        return await runtime.remember.save(ctx, request)

    @app.post("/p3/recall", response_model=ContextPack)
    async def recall(
        request: RecallRequest, ctx: TrustedContext = trusted_dependency
    ) -> ContextPack:
        return await runtime.recall.recall(ctx, request)

    @app.get("/p3/recalls/{recall_id}", response_model=RecallRecord)
    def recall_status(
        recall_id: Identifier, ctx: TrustedContext = trusted_dependency
    ) -> RecallRecord:
        # 通过统一认证依赖查询状态；实际对象权限由 Recall 服务继续校验。
        return runtime.recall.status(ctx, recall_id)

    @app.get("/p3/recalls/{recall_id}/result", response_model=ContextPack)
    def recall_result(
        recall_id: Identifier, ctx: TrustedContext = trusted_dependency
    ) -> ContextPack:
        # 结果重取必须走服务层最终复核，不能把数据库中的历史包直接作为 HTTP 响应。
        return runtime.recall.result(ctx, recall_id)

    if hasattr(runtime.remember, "read_source"):
        from aether_agent_memory.remember.http import attach_routes

        attach_routes(app, runtime.remember, trusted_dependency)
    app.state.trusted_dependency = trusted_dependency
    return app
