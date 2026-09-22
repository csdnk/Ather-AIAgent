"""Remember-owned vector projection writes, verification and cleanup."""

import json
import math

from aether_agent_memory.remember.contracts.models import (
    MemoryRef,
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
)
from aether_agent_memory.remember.contracts.ports import ProjectionPort
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.vector_backend import SPACE, MilvusConnection, SQLiteVectorStore


class SQLiteProjection(SQLiteVectorStore):
    async def project(self, ctx: TrustedContext, request: ProjectionRequest) -> ProjectionResult:
        self.check(ctx)
        if (
            request.deadline_at > ctx.deadline_at
            or request.target.model_space != self.model_space
            or len(request.vector) != self.dimensions
            or not all(math.isfinite(v) for v in request.vector)
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid projection binding")
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.READ, self.ref(request.target))
            data = {
                "target": request.target.model_dump(mode="json"),
                "vector": list(request.vector),
            }
            existing = tx.read("recall_vectors", request.target.vector_id)
            if existing and existing != data:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "vector ID content changed")
            tx.write("recall_vectors", request.target.vector_id, data)
        return await self.inspect(ctx, request.target, request.operation_id)

    async def inspect(
        self, ctx: TrustedContext, target: ProjectionTarget, operation_id: str
    ) -> ProjectionResult:
        self.check(ctx)
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.READ, self.ref(target))
            stored = tx.read("recall_vectors", target.vector_id)
        exact = stored is not None and stored["target"] == target.model_dump(mode="json")
        return ProjectionResult(
            operation_id=operation_id,
            target=target,
            state="verified" if exact else "absent",
            payload_matches=exact,
            searchable=exact,
            observed_at=self.identity.clock(),
        )

    async def delete(
        self, ctx: TrustedContext, target: ProjectionTarget, operation_id: str
    ) -> ProjectionResult:
        self.check(ctx)
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.DELETE, self.ref(target))
            tx.raw.delete("p3_rf_recall_vectors", "system", target.vector_id)
        return await self.inspect(ctx, target, operation_id)


class MilvusProjection(MilvusConnection):
    def guard(self, ctx: TrustedContext, target: ProjectionTarget, permission: Permission) -> None:
        expected = projection_target(target.memory, target.input_hash, target.model_space)
        if target != expected or target.model_space != self.model_space:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid projection identity")
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, permission, SQLiteVectorStore.ref(target))

    async def project(self, ctx: TrustedContext, request: ProjectionRequest) -> ProjectionResult:
        self.guard(ctx, request.target, Permission.READ)
        if (
            len(request.vector) != self.dimensions
            or not all(math.isfinite(v) for v in request.vector)
            or request.deadline_at > ctx.deadline_at
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid projection vector")
        data = {"target": request.target.model_dump(mode="json"), "vector": list(request.vector)}
        key = request.target.vector_id
        with self.uow.transaction() as tx:
            prior = tx.read("milvus_projections", key)
            if prior and (prior["data"] != data or prior["deleted"]):
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "projection changed or deleted")
            tx.write("milvus_projections", key, {"data": data, "deleted": False})
        await self.prepare(ctx)
        row = {
            "vector_id": key,
            "vector": list(request.vector),
            "target": data["target"],
            "model_space": self.model_space,
            **{k: v or "" for k, v in request.target.memory.scope.model_dump().items()},
        }
        try:
            await self.call(ctx, "upsert", data=[row])
        except FoundationError as exc:
            if exc.code != ErrorCode.DEPENDENCY_UNAVAILABLE:
                raise
            return self.result(request.target, request.operation_id, "unknown")
        return await self.inspect(ctx, request.target, request.operation_id)

    def result(self, target: ProjectionTarget, operation_id: str, state: str) -> ProjectionResult:
        return ProjectionResult.model_validate(
            {
                "operation_id": operation_id,
                "target": target,
                "state": state,
                "payload_matches": state == "verified",
                "searchable": state == "verified",
                "observed_at": self.identity.clock(),
            }
        )

    async def inspect(
        self,
        ctx: TrustedContext,
        target: ProjectionTarget,
        operation_id: str,
    ) -> ProjectionResult:
        self.guard(ctx, target, Permission.READ)
        await self.prepare(ctx)
        rows = await self.call(
            ctx,
            "query",
            filter="vector_id == " + json.dumps(target.vector_id),
            output_fields=["target", "vector"],
            consistency_level="Strong",
            limit=1,
        )
        if not rows:
            return self.result(target, operation_id, "absent")
        with self.uow.transaction() as tx:
            intent = tx.read("milvus_projections", target.vector_id)
        if (
            not intent
            or "vector" not in intent["data"]
            or rows[0]["target"] != target.model_dump(mode="json")
            or len(rows[0]["vector"]) != self.dimensions
            or not all(
                math.isclose(float(a), float(b), rel_tol=1e-5, abs_tol=1e-7)
                for a, b in zip(rows[0]["vector"], intent["data"]["vector"], strict=True)
            )
        ):
            return self.result(target, operation_id, "failed")
        hits = await self.call(
            ctx,
            "search",
            data=[rows[0]["vector"]],
            anns_field="vector",
            filter="vector_id == " + json.dumps(target.vector_id),
            limit=1,
            output_fields=["target"],
            search_params={"metric_type": "IP"},
            consistency_level="Strong",
        )
        verified = bool(hits and hits[0] and hits[0][0]["entity"]["target"] == rows[0]["target"])
        return self.result(target, operation_id, "verified" if verified else "pending")

    async def delete(
        self,
        ctx: TrustedContext,
        target: ProjectionTarget,
        operation_id: str,
    ) -> ProjectionResult:
        self.guard(ctx, target, Permission.DELETE)
        with self.uow.transaction() as tx:
            row = tx.read("milvus_projections", target.vector_id)
            tx.write(
                "milvus_projections",
                target.vector_id,
                {
                    "data": row["data"] if row else {"target": target.model_dump(mode="json")},
                    "deleted": True,
                },
            )
        await self.prepare(ctx)
        await self.call(ctx, "delete", ids=[target.vector_id])
        return await self.inspect(ctx, target, operation_id)


def projection_target(
    memory: MemoryRef, content_hash: str, model_space: str = SPACE
) -> ProjectionTarget:
    return ProjectionTarget(
        memory=memory,
        model_space=model_space,
        chunk_index=0,
        input_hash=content_hash,
        vector_id=fingerprint([memory.model_dump(mode="json"), model_space, content_hash]),
    )


class ProjectionAccess:
    """Expose only B's write/inspect/delete contract, even for a combined legacy backend."""

    def __init__(self, provider: ProjectionPort) -> None:
        self._provider = provider

    async def project(self, ctx: TrustedContext, request: ProjectionRequest) -> ProjectionResult:
        return await self._provider.project(ctx, request)

    async def inspect(
        self, ctx: TrustedContext, target: ProjectionTarget, operation_id: str
    ) -> ProjectionResult:
        return await self._provider.inspect(ctx, target, operation_id)

    async def delete(
        self, ctx: TrustedContext, target: ProjectionTarget, operation_id: str
    ) -> ProjectionResult:
        return await self._provider.delete(ctx, target, operation_id)
