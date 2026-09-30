"""Authenticated HTTP composition over the same P3 services and worker loop."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
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
    PageRequest,
    RecoveryRequest,
    TaskOperationView,
    TaskRecord,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, now
from aether_agent_memory.runtime.temporal.controls import ControlRequest

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
    execution: Any = None,
) -> FastAPI:
    """Caller owns runtime lifetime. Credentials are supplied at use, never logged."""
    execution = execution or getattr(runtime, "execution", None)
    if execution is None:
        raise ValueError("HTTP requires a configured Temporal execution service")
    supervisor = execution
    state = execution.state

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await execution.start()
            yield
        finally:
            await execution.stop()
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
            retryable=status in {503, 504} or error.code == ErrorCode.REQUEST_IN_PROGRESS,
            request_id=trusted.request_id if trusted else secrets.token_hex(16),
            operation_id=trusted.operation_id if trusted else None,
        )
        job_id = getattr(error, "job_id", None)
        headers = (
            {"Location": f"/p3/operations/{job_id}", "X-P3-Job-ID": job_id} if job_id else None
        )
        return JSONResponse(
            status_code=status, content=result.model_dump(mode="json"), headers=headers
        )

    @app.get("/p3/live")
    def live() -> dict[str, str]:
        return {"liveness": "alive", "checked_at": now()}

    @app.get("/p3/readyz")
    def readyz() -> JSONResponse:
        result = (
            execution.ready()
            if execution is not None
            else {"readiness": "not_ready", "reason_code": "TEMPORAL_NOT_CONFIGURED"}
        )
        return JSONResponse(
            status_code=200 if result["readiness"] == "ready" else 503, content=result
        )

    @app.middleware("http")
    async def admission_gate(request: Request, call_next: Any) -> Any:
        if execution is not None and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                execution.require_ready()
            except FoundationError as exc:
                return await foundation_error(request, exc)
        return await call_next(request)

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

    @app.get("/p3/traces")
    def traces(
        flow: Literal["business", "remember", "recall", "operate", "runtime"] | None = None,
        before: int | None = Query(default=None, ge=1),
        limit: int = Query(default=50, ge=1, le=100),
        ctx: TrustedContext = trusted_dependency,
    ) -> dict[str, Any]:
        return runtime.foundation.monitoring.traces(ctx, before=before, limit=limit, flow=flow)

    @app.get("/p3/tasks")
    def tasks(
        state: TaskState | None = None,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        ctx: TrustedContext = trusted_dependency,
    ) -> dict[str, Any]:
        return runtime.foundation.diagnostics.tasks_page(
            ctx, PageRequest(limit=limit, cursor=cursor), state
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
    async def recovery(
        request: RecoveryRequest, ctx: TrustedContext = trusted_dependency
    ) -> OperationRecord:
        with runtime.foundation.uow.transaction() as tx:
            result = runtime.foundation.tasks.request_recovery(tx, ctx, request)
        await execution.bridge.flush()
        return OperationRecord.model_validate(execution.controls.status(ctx, result.operation_id))

    @app.post("/p3/tasks/{task_id}/control", response_model=OperationRecord)
    async def task_control(
        task_id: str, request: ControlRequest, ctx: TrustedContext = trusted_dependency
    ) -> OperationRecord:
        with runtime.foundation.uow.transaction() as tx:
            result = execution.controls.task(tx, ctx, task_id, request)
        await execution.bridge.flush()
        return OperationRecord.model_validate(execution.controls.status(ctx, result.operation_id))

    @app.get("/p3/controls/{operation_id}", response_model=OperationRecord)
    def control_status(
        operation_id: str, ctx: TrustedContext = trusted_dependency
    ) -> OperationRecord:
        return OperationRecord.model_validate(execution.controls.status(ctx, operation_id))

    @app.get("/p3/periodic/control")
    def periodic_control_status(ctx: TrustedContext = trusted_dependency) -> dict[str, Any]:
        with runtime.foundation.uow.transaction() as tx:
            return dict(execution.controls.periodic_snapshot(tx, ctx))

    @app.post("/p3/periodic/control", response_model=OperationRecord)
    async def periodic_control(
        request: ControlRequest, ctx: TrustedContext = trusted_dependency
    ) -> OperationRecord:
        with runtime.foundation.uow.transaction() as tx:
            result = execution.controls.periodic(tx, ctx, request)
        await execution.bridge.flush()
        return OperationRecord.model_validate(execution.controls.status(ctx, result.operation_id))

    @app.get("/p3/incidents", response_model=tuple[IncidentRecord, ...])
    def incidents(ctx: TrustedContext = trusted_dependency) -> tuple[IncidentRecord, ...]:
        return runtime.foundation.dispositions.incidents(ctx)

    @app.post("/p3/maintenance/cycle")
    async def cycle(ctx: TrustedContext = trusted_dependency) -> dict[str, int]:
        runtime.foundation.diagnostics.authorize(ctx)
        execution.require_ready()
        raise HTTPException(
            status_code=410,
            detail="Manual RF cycles are retired; use the Temporal periodic workflow",
        )

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
        request: RememberRequest, response: Response, ctx: TrustedContext = trusted_dependency
    ) -> RememberReceipt:
        return RememberReceipt.model_validate(
            await execution.execute(
                ctx, "remember.save", request.model_dump(mode="json"), response.headers
            )
        )

    @app.post("/p3/recall", response_model=ContextPack)
    async def recall(
        request: RecallRequest, response: Response, ctx: TrustedContext = trusted_dependency
    ) -> ContextPack:
        return ContextPack.model_validate(
            await execution.execute(
                ctx, "recall.execute", request.model_dump(mode="json"), response.headers
            )
        )

    @app.get("/p3/operations/{job_id}", response_model=TaskOperationView)
    def operation_status(job_id: Identifier, ctx: TrustedContext = trusted_dependency) -> Any:
        if execution is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Temporal not configured")
        return execution.operation(ctx, job_id)

    @app.get("/p3/operations/{job_id}/result")
    def operation_result(job_id: Identifier, ctx: TrustedContext = trusted_dependency) -> Any:
        if execution is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Temporal not configured")
        return execution.result(ctx, job_id)

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

        attach_routes(app, runtime.remember, trusted_dependency, execution=execution)
    app.state.trusted_dependency = trusted_dependency
    return app
