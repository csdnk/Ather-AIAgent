"""Milvus VectorPort. SQLite owns intent; remote visibility is verified separately."""

import asyncio
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import BoundedSemaphore
from typing import Any

from aether_agent_memory.recall.contracts.models import (
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
    VectorCandidate,
    VectorSearchRequest,
    VectorSearchResult,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import matches, select_scope
from aether_agent_memory.runtime.foundation.storage import SQLiteUnitOfWork

from .adapters import SQLiteVectors, projection_target


class MilvusVectors:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        model_space: str,
        dimensions: int,
        *,
        uri: str,
        collection: str = "p3_memories",
        token: str = "",
        client: Any = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", collection):
            raise ValueError("invalid Milvus collection")
        self.uow, self.identity = uow, identity
        self.model_space, self.dimensions, self.collection = model_space, dimensions, collection
        if client is None:
            from pymilvus import MilvusClient

            client = MilvusClient(uri=uri, token=token, timeout=10)
        self.client: Any = client
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="p3-milvus")
        self.slots = BoundedSemaphore(4)
        self.prepare_lock = asyncio.Lock()
        self.closed = False
        self.prepared = False

    def timeout(self, ctx: TrustedContext) -> float:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
        seconds = (
            datetime.fromisoformat(ctx.deadline_at.replace("Z", "+00:00"))
            - datetime.fromisoformat(self.identity.clock().replace("Z", "+00:00"))
        ).total_seconds()
        return max(0.001, min(seconds, 10))

    async def call(self, ctx: TrustedContext, method: str, **kwargs: Any) -> Any:
        timeout = self.timeout(ctx)
        if self.closed or not self.slots.acquire(blocking=False):
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Milvus adapter closed or busy")

        def invoke() -> Any:
            return getattr(self.client, method)(
                collection_name=self.collection, timeout=timeout, **kwargs
            )

        try:
            future = self.executor.submit(invoke)
        except BaseException:
            self.slots.release()
            raise
        future.add_done_callback(lambda _: self.slots.release())
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        try:
            result = await asyncio.wait_for(asyncio.shield(wrapped), timeout)
        except Exception as exc:
            if isinstance(exc, FoundationError):
                raise
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Milvus call failed") from exc
        self.timeout(ctx)  # Recheck revocation/deadline after the remote boundary.
        return result

    async def prepare(self, ctx: TrustedContext) -> None:
        if self.prepared:
            return
        async with self.prepare_lock:
            if not self.prepared:
                await self.prepare_collection(ctx)

    async def prepare_collection(self, ctx: TrustedContext) -> None:
        if not await self.call(ctx, "has_collection"):
            from pymilvus import DataType

            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("vector_id", DataType.VARCHAR, is_primary=True, max_length=64)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dimensions)
            schema.add_field("target", DataType.JSON)
            schema.add_field("model_space", DataType.VARCHAR, max_length=512)
            for field in (
                "tenant_id",
                "application_id",
                "user_id",
                "agent_id",
                "session_id",
                "task_id",
            ):
                schema.add_field(field, DataType.VARCHAR, max_length=512)
            index = self.client.prepare_index_params()
            index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="IP")
            await self.call(
                ctx,
                "create_collection",
                schema=schema,
                index_params=index,
                consistency_level="Strong",
            )
        description = await self.call(ctx, "describe_collection")
        fields = {f["name"]: f for f in description["fields"]}
        if int(fields.get("vector", {}).get("params", {}).get("dim", 0)) != self.dimensions or not {
            "target",
            "vector_id",
            "model_space",
            "tenant_id",
            "application_id",
            "user_id",
            "agent_id",
            "session_id",
            "task_id",
        }.issubset(fields):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Milvus collection schema mismatch")
        await self.call(ctx, "load_collection")
        self.prepared = True

    def guard(self, ctx: TrustedContext, target: ProjectionTarget, permission: Permission) -> None:
        expected = projection_target(target.memory, target.input_hash, target.model_space)
        if target != expected or target.model_space != self.model_space:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid projection identity")
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, permission, SQLiteVectors.ref(target))

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

    async def search(self, ctx: TrustedContext, request: VectorSearchRequest) -> VectorSearchResult:
        scope = select_scope(ctx, request.selection)
        if (
            request.model_space != self.model_space
            or len(request.vector) != self.dimensions
            or not all(math.isfinite(v) for v in request.vector)
            or request.deadline_at > ctx.deadline_at
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid vector search")
        await self.prepare(ctx)
        # Field names are constant; values are JSON-escaped, never raw caller expressions.
        conditions = [
            f"{field} == {json.dumps(value, ensure_ascii=True)}"
            for field, value in scope.model_dump().items()
            if value is not None
        ]
        conditions.append("model_space == " + json.dumps(self.model_space))
        hits = await self.call(
            ctx,
            "search",
            data=[list(request.vector)],
            anns_field="vector",
            filter=" and ".join(conditions),
            limit=request.limit,
            output_fields=["target"],
            search_params={"metric_type": "IP"},
            consistency_level="Strong",
        )
        items: list[VectorCandidate] = []
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            for hit in hits[0] if hits else []:
                target = ProjectionTarget.model_validate(hit["entity"]["target"])
                if (
                    target.model_space != self.model_space
                    or not matches(target.memory.scope, scope)
                    or not self.identity.permits(
                        tx, ctx, Permission.READ, SQLiteVectors.ref(target)
                    )
                ):
                    continue
                score = float(hit["distance"])
                if not math.isfinite(score):
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "nonfinite Milvus score")
                if score > 0:
                    items.append(VectorCandidate(target=target, rank=len(items) + 1, score=score))
        return VectorSearchResult(candidates=tuple(items), coverage="complete")

    async def health(self, ctx: TrustedContext) -> dict[str, object]:
        exists = await self.call(ctx, "has_collection")
        return {"state": "available" if exists else "unknown", "provider_id": "milvus"}

    def close(self) -> None:
        self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.client.close()
