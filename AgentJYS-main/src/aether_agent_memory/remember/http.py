"""Remember routes; the host supplies its existing authenticated dependency."""

from typing import Any

from fastapi import Depends, FastAPI, Response

from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    DeleteRequest,
    LifecycleRequest,
    MemoryRef,
    ReflectionRequest,
    RetentionRequest,
    SourceRef,
)
from aether_agent_memory.runtime.contracts.http_evidence import HttpRequestEvidence
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Identifier,
    ScopeSelector,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.mutation_receipts import (
    MutationKind,
    MutationLookup,
    MutationResult,
)
from aether_agent_memory.runtime.flows.http_evidence import capture_http_request
from aether_agent_memory.runtime.foundation.common import FoundationError

from .basic.pipeline import RememberPipeline
from .basic.service import Remember


def attach_routes(
    app: FastAPI, provider: Remember, trusted_dependency: Any, *, execution: Any = None
) -> None:
    if not isinstance(provider, RememberPipeline):
        raise TypeError("Remember routes require the pipeline provider")
    remember = provider
    http_dependency = Depends(capture_http_request)

    @app.get("/p3/mutation-receipts/{operation_id}", response_model=MutationLookup)
    def mutation_receipt(
        operation_id: Identifier, kind: MutationKind, ctx: TrustedContext = trusted_dependency
    ) -> MutationLookup:
        return remember.mutations.lookup(ctx, operation_id, kind)

    @app.get("/p3/mutation-receipts/{operation_id}/result", response_model=MutationResult)
    def mutation_result(
        operation_id: Identifier,
        kind: MutationKind,
        response: Response,
        ctx: TrustedContext = trusted_dependency,
    ) -> MutationResult:
        result = remember.mutations.result(ctx, operation_id, kind)
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.post("/p3/remember/consolidate")
    def consolidate(
        selection: ScopeSelector,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> dict[str, Any]:
        return {"task_ids": remember.consolidate(ctx, selection, http_request=http_request)}

    @app.post("/p3/remember/reflection")
    def reflection_configure(
        request: ReflectionRequest,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> dict[str, Any]:
        return remember.reflection.configure(ctx, request, http_request=http_request)

    @app.get("/p3/remember/reflection")
    def reflection_read(
        session_id: Identifier | None = None,
        task_id: Identifier | None = None,
        ctx: TrustedContext = trusted_dependency,
    ) -> dict[str, Any]:
        return remember.reflection.read(ctx, ScopeSelector(session_id=session_id, task_id=task_id))

    @app.post("/p3/remember/distill")
    def distill(
        refs: tuple[MemoryRef, ...],
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> dict[str, Any]:
        return {"task_id": remember.distill(ctx, refs, http_request=http_request)}

    @app.get("/p3/remember/{memory_id}")
    def memory(memory_id: str, ctx: TrustedContext = trusted_dependency) -> Any:
        return remember.get(ctx, memory_id)

    @app.get("/p3/remember/{memory_id}/processing")
    def processing(memory_id: str, ctx: TrustedContext = trusted_dependency) -> dict[str, Any]:
        return remember.processing(ctx, memory_id)

    @app.post("/p3/remember/body")
    async def body(ref: MemoryRef, ctx: TrustedContext = trusted_dependency) -> Any:
        return await remember.read_body(ctx, ref)

    @app.post("/p3/remember/body/range")
    async def body_range(
        ref: MemoryRef,
        start: int = 0,
        end: int | None = None,
        ctx: TrustedContext = trusted_dependency,
    ) -> Any:
        return await remember.read_range(ctx, ref, start, end)

    @app.post("/p3/sources/read-range")
    async def source_range(
        ref: SourceRef,
        start: int = 0,
        end: int | None = None,
        ctx: TrustedContext = trusted_dependency,
    ) -> Any:
        return await remember.read_source(ctx, ref, start, end)

    @app.post("/p3/remember/{memory_id}/correct")
    async def correct(
        memory_id: str,
        request: CorrectionRequest,
        response: Response,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> Any:
        if execution is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Temporal execution required")
        return await execution.execute(
            ctx,
            "remember.correct",
            {"memory_id": memory_id, "request": request.model_dump(mode="json")},
            response.headers,
            http_request=http_request,
        )

    @app.get("/p3/remember/{memory_id}/retention")
    def retention_read(memory_id: str, ctx: TrustedContext = trusted_dependency) -> dict[str, Any]:
        return remember.retention.read(ctx, memory_id)

    @app.post("/p3/remember/{memory_id}/retention")
    def retention_configure(
        memory_id: str,
        request: RetentionRequest,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> dict[str, Any]:
        return remember.retention.configure(ctx, memory_id, request, http_request=http_request)

    @app.post("/p3/remember/{memory_id}/lifecycle")
    def lifecycle(
        memory_id: str,
        request: LifecycleRequest,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> Any:
        return remember.lifecycle(ctx, memory_id, request, http_request=http_request)

    @app.post("/p3/remember/{memory_id}/delete")
    def delete(
        memory_id: str,
        request: DeleteRequest,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> Any:
        return remember.delete(ctx, memory_id, request, http_request=http_request)

    @app.post("/p3/remember/{memory_id}/reprocess")
    def reprocess(
        memory_id: str,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> dict[str, Any]:
        return {"task_id": remember.reprocess(ctx, memory_id, http_request=http_request)}

    @app.post("/p3/remember/{memory_id}/reindex")
    def reindex(
        memory_id: str,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> dict[str, Any]:
        return {
            "task_id": remember.reindex(ctx, memory_id, http_request=http_request),
            "phase": "processing",
        }

    @app.post("/p3/sources/{source_id}/delete")
    def delete_source(
        source_id: str,
        request: DeleteRequest,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> Any:
        return remember.delete_source(ctx, source_id, request, http_request=http_request)

    @app.post("/p3/sources/{source_id}/revoke")
    def revoke_source(
        source_id: str,
        request: DeleteRequest,
        ctx: TrustedContext = trusted_dependency,
        http_request: HttpRequestEvidence = http_dependency,
    ) -> Any:
        return remember.revoke_source(ctx, source_id, request, http_request=http_request)
