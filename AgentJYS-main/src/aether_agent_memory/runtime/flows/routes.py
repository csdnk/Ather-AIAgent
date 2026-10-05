"""Catalog, document ingress and scoped operations queries on the same runtime."""

from hashlib import sha256
from typing import Any

from fastapi import FastAPI, Request, Response

from aether_agent_memory.p2.client import P2GrpcClient
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Identifier,
    PageRequest,
    Permission,
    RecordRef,
    ScopeSelector,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError

from .http_evidence import evidence_from_hash


def attach(app: FastAPI, service: Any) -> None:
    from .client_run_routes import attach as attach_client_runs

    runtime = service.runtime
    dependency = app.state.trusted_dependency
    attach_client_runs(
        app,
        runtime.foundation,
        objects=service.execution.inputs.objects,
        max_bytes=runtime.remember.policy.max_input_bytes,
    )

    @app.get("/p3/capabilities")
    def capabilities(ctx: TrustedContext = dependency) -> dict[str, Any]:
        with runtime.foundation.uow.transaction() as tx:
            runtime.foundation.identity.revalidate(tx, ctx)
        objects = runtime.remember.bodies.p2
        object_storage = (
            "p2_grpc"
            if isinstance(objects, P2GrpcClient)
            else getattr(objects, "provider_id", "unconfigured")
        )
        return {
            "profile": service.config.profile,
            "storage_mode": service.config.storage_mode,
            "production_ready": False,
            "metadata_storage": service.config.metadata_backend,
            "telemetry_storage": service.config.metadata_backend,
            "embedding": runtime.embedding_profile,
            "semantic_processing": "model" if service.config.language_model else "literal_baseline",
            "object_storage": object_storage,
            "scheduling": "temporal_v1",
            "executor": runtime.executor.provider_id,
            "operations": [
                "remember",
                "recall",
                "documents",
                "correction",
                "lifecycle",
                "deletion",
                "retention",
                "reflection",
                "recovery",
            ],
        }

    @app.put("/p3/documents/{document_id}")
    async def upload(
        document_id: Identifier,
        version: str,
        request: Request,
        response: Response,
        ctx: TrustedContext = dependency,
    ) -> Any:
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > runtime.remember.policy.max_input_bytes:
                raise FoundationError(ErrorCode.INVALID_ARGUMENT, "document exceeds ingress limit")
        return await service.execution.upload(
            ctx,
            document_id,
            version,
            bytes(chunks),
            request.headers.get("content-type", "application/octet-stream"),
            response.headers,
            http_request=evidence_from_hash(request, sha256(chunks).hexdigest(), version=version),
        )

    @app.get("/p3/memories")
    def memories(
        session_id: Identifier | None = None,
        task_id: Identifier | None = None,
        limit: int = 50,
        cursor: str | None = None,
        ctx: TrustedContext = dependency,
    ) -> dict[str, Any]:
        selection = ScopeSelector(session_id=session_id, task_id=task_id)
        with runtime.foundation.uow.transaction() as tx:
            runtime.foundation.identity.revalidate(tx, ctx)
            values = []
            for key, pointer in tx.rows("remember_current"):
                ref = RecordRef.model_validate(pointer)
                if not runtime.foundation.identity.discoverable(tx, ctx, ref, selection):
                    continue
                raw = tx.get(ref)
                if raw is None:
                    continue
                # Metadata catalog does not hydrate, leak or resurrect deleted bodies.
                values.append(
                    (
                        key,
                        {
                            k: v
                            for k, v in raw.items()
                            if k
                            in {
                                "ref",
                                "kind",
                                "status",
                                "revision",
                                "object_revision",
                                "projection_state",
                                "created_at",
                                "expires_at",
                                "importance",
                            }
                        },
                    )
                )
            rows, following = tx.page(
                values,
                [
                    "memory_catalog",
                    ctx.principal.model_dump(mode="json"),
                    selection.model_dump(mode="json"),
                ],
                PageRequest(limit=limit, cursor=cursor),
            )
        return {"items": rows, "next_cursor": following}

    @app.get("/p3/operate/memories/{memory_id}")
    def placement(memory_id: Identifier, ctx: TrustedContext = dependency) -> dict[str, Any]:
        item = runtime.remember.get(ctx, memory_id)
        key = runtime.operate.key(item.ref)
        with runtime.foundation.uow.transaction() as tx:
            runtime.foundation.identity.authorize(
                tx,
                ctx,
                Permission.DIAGNOSE,
                RecordRef(
                    owner="remember",
                    object_type="memory",
                    object_id=memory_id,
                    scope=item.ref.scope,
                ),
            )
            view = tx.read("operate_views", key)
            heat = tx.read("operate_heat", key)
            actions = [
                row
                for _, row in tx.rows("operate_actions")
                if MemoryRef.model_validate(row["intent"]["decision"]["memory"]) == item.ref
            ]
        return {
            "memory": item.ref,
            "input": {k: v for k, v in (view or {}).items() if k != "event"},
            "heat": heat,
            "actions": actions,
        }
