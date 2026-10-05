"""Remember-owned vector projection writes, verification and cleanup."""

import json
import math
from typing import Any, Literal, cast

from aether_agent_memory.remember.contracts.models import (
    MemoryRef,
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
)
from aether_agent_memory.remember.contracts.ports import ProjectionPort
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.vector_backend import MilvusConnection, projection_ref


class MilvusProjection(MilvusConnection):
    def guard(self, ctx: TrustedContext, target: ProjectionTarget, permission: Permission) -> None:
        expected = projection_target(
            target.memory,
            target.input_hash,
            target.model_space,
            chunk_index=target.chunk_index,
            generation=target.generation,
            body_hash=target.body_hash,
            memory_source=target.memory_source,
        )
        if target != expected or target.model_space != self.model_space:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid projection identity")
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, permission, projection_ref(target))

    async def project(self, ctx: TrustedContext, request: ProjectionRequest) -> ProjectionResult:
        await self.metadata(lambda: self.guard(ctx, request.target, Permission.READ))
        if (
            len(request.vector) != self.dimensions
            or not all(math.isfinite(v) for v in request.vector)
            or request.deadline_at > ctx.deadline_at
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid projection vector")
        data = {"target": request.target.model_dump(mode="json"), "vector": list(request.vector)}
        key = request.target.vector_id

        def reserve() -> None:
            with self.uow.transaction() as tx:
                self.identity.authorize(tx, ctx, Permission.READ, projection_ref(request.target))
                prior = tx.read(self.projection_namespace, key)
                if prior and (
                    ProjectionTarget.model_validate(prior["data"]["target"]) != request.target
                    or prior["data"].get("vector") != data["vector"]
                    or prior["deleted"]
                ):
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "projection changed or deleted")
                tx.write(self.projection_namespace, key, {"data": data, "deleted": False})

        await self.metadata(reserve)
        await self.prepare(ctx)
        row = {
            "vector_id": key,
            "vector": list(request.vector),
            "target": data["target"],
            "model_space": self.model_space,
            **{k: v or "" for k, v in request.target.memory.scope.model_dump().items()},
        }

        def before_upsert() -> None:
            self.guard(ctx, request.target, Permission.READ)
            with self.uow.transaction() as tx:
                current = tx.read(self.projection_namespace, key)
                if (
                    not current
                    or current["deleted"]
                    or ProjectionTarget.model_validate(current["data"]["target"]) != request.target
                    or current["data"].get("vector") != data["vector"]
                ):
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "projection changed or deleted")

        try:
            await self.call(ctx, "upsert", before_call=before_upsert, data=[row])
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
        await self.metadata(lambda: self.guard(ctx, target, Permission.READ))
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

        def read_intent() -> dict[str, Any] | None:
            with self.uow.transaction() as tx:
                self.identity.authorize(tx, ctx, Permission.READ, projection_ref(target))
                return cast(
                    dict[str, Any] | None, tx.read(self.projection_namespace, target.vector_id)
                )

        intent = await self.metadata(read_intent)
        if (
            not intent
            or "vector" not in intent["data"]
            or ProjectionTarget.model_validate(intent["data"]["target"]) != target
            or ProjectionTarget.model_validate(rows[0]["target"]) != target
            or intent["deleted"]
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
        verified = bool(
            hits
            and hits[0]
            and ProjectionTarget.model_validate(hits[0][0]["entity"]["target"]) == target
        )
        return self.result(target, operation_id, "verified" if verified else "pending")

    async def delete(
        self,
        ctx: TrustedContext,
        target: ProjectionTarget,
        operation_id: str,
    ) -> ProjectionResult:
        await self.metadata(lambda: self.guard(ctx, target, Permission.DELETE))

        def reserve_delete() -> None:
            with self.uow.transaction() as tx:
                self.identity.authorize(tx, ctx, Permission.DELETE, projection_ref(target))
                row = tx.read(self.projection_namespace, target.vector_id)
                tx.write(
                    self.projection_namespace,
                    target.vector_id,
                    {
                        "data": row["data"] if row else {"target": target.model_dump(mode="json")},
                        "deleted": True,
                    },
                )

        await self.metadata(reserve_delete)
        await self.prepare(ctx)
        await self.call(
            ctx,
            "delete",
            before_call=lambda: self.guard(ctx, target, Permission.DELETE),
            ids=[target.vector_id],
        )
        return await self.inspect(ctx, target, operation_id)


def projection_target(
    memory: MemoryRef,
    content_hash: str,
    model_space: str,
    *,
    chunk_index: int = 0,
    generation: str | None = None,
    body_hash: str | None = None,
    memory_source: Literal["working", "long_term"] = "long_term",
) -> ProjectionTarget:
    return ProjectionTarget(
        memory=memory,
        model_space=model_space,
        chunk_index=chunk_index,
        input_hash=content_hash,
        vector_id=fingerprint(
            [memory.model_dump(mode="json"), model_space, content_hash]
            + ([generation, chunk_index, body_hash] if generation else [])
            + (["working"] if memory_source == "working" else [])
        ),
        generation=generation,
        body_hash=body_hash,
        memory_source=memory_source,
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
