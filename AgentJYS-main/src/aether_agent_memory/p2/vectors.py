"""Development P3 consumer of the unchanged P2 VectorService contract.

P2 v0.1 lacks scoped server filtering, exact vector fetch and operation queries.
A P3 reference journal binds requests and P2's returned metadata checks the
submitted payload. Bounded search never upgrades missing evidence into absence.
This adapter is deliberately unavailable to production deployment configuration.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, TypeVar

from aether_agent_memory.p2.client import P2GrpcClient, P2VectorHit
from aether_agent_memory.p2.models import EmbeddingRecord
from aether_agent_memory.recall.contracts.foundation import (
    ChunkHit,
    ChunkSearchRequest,
    ChunkSearchResult,
)
from aether_agent_memory.recall.contracts.models import (
    VectorCandidate,
    VectorSearchRequest,
    VectorSearchResult,
)
from aether_agent_memory.remember.basic.projection import projection_target
from aether_agent_memory.remember.contracts.models import (
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    PageRequest,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.requests import select_scope

SCHEMA = "p3_current_p2_v1"
T = TypeVar("T")


class CurrentP2Vectors:
    def __init__(
        self,
        uow: Any,
        identity: Any,
        model_space: str,
        dimensions: int,
        client: P2GrpcClient,
        *,
        max_hits: int = 1000,
    ) -> None:
        if not 1 <= max_hits <= 10000 or dimensions <= 0:
            raise ValueError("invalid P2 vector dimensions or candidate bound")
        self.uow, self.identity = uow, identity
        self.model_space, self.dimensions = model_space, dimensions
        self.available = True
        self.client, self.max_hits = client, max_hits

    def check(self, ctx: TrustedContext) -> None:
        if not self.available:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 vector client is closed")
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)

    def request_context(self, ctx: TrustedContext, deadline_at: str) -> TrustedContext:
        if deadline_at > ctx.deadline_at:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "P2 request extends caller deadline")
        narrowed = ctx.model_copy(update={"deadline_at": deadline_at})
        self.check(narrowed)
        return narrowed

    async def call(
        self, ctx: TrustedContext, method: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any
    ) -> T:
        self.check(ctx)
        remaining = (
            datetime.fromisoformat(ctx.deadline_at.replace("Z", "+00:00"))
            - datetime.fromisoformat(self.identity.clock().replace("Z", "+00:00"))
        ).total_seconds()
        try:
            async with asyncio.timeout(remaining):
                result = await method(*args, **kwargs)
        except TimeoutError as exc:
            # Cancellation cannot prove a remote write had no effect. The caller's
            # durable intent remains available for inspection under its original ID.
            raise FoundationError(
                ErrorCode.DEADLINE_EXCEEDED, "P2 request deadline expired"
            ) from exc
        self.check(ctx)
        return result

    @staticmethod
    def ref(target: ProjectionTarget) -> RecordRef:
        return RecordRef(
            owner=Flow.REMEMBER,
            object_type="memory",
            object_id=target.memory.memory_id,
            scope=target.memory.scope,
        )

    def binding(self) -> dict[str, object]:
        return {
            "provider": "current_p2",
            "endpoint_hash": fingerprint(self.client.endpoint),
            "collection": self.client.collection,
            "schema": SCHEMA,
            "model_space": self.model_space,
        }

    def guard(self, ctx: TrustedContext, target: ProjectionTarget, permission: Permission) -> None:
        self.check(ctx)
        if target.model_space != self.model_space or target != projection_target(
            target.memory,
            target.input_hash,
            target.model_space,
            chunk_index=target.chunk_index,
            generation=target.generation,
            body_hash=target.body_hash,
            memory_source=target.memory_source,
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid P2 projection identity")
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, permission, self.ref(target))

    def result(
        self, ctx: TrustedContext, target: ProjectionTarget, operation_id: str, state: str
    ) -> ProjectionResult:
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

    async def project(self, ctx: TrustedContext, request: ProjectionRequest) -> ProjectionResult:
        ctx = self.request_context(ctx, request.deadline_at)
        self.guard(ctx, request.target, Permission.READ)
        if (
            len(request.vector) != self.dimensions
            or not all(math.isfinite(value) for value in request.vector)
            or not math.isclose(sum(value * value for value in request.vector), 1.0, abs_tol=1e-4)
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid P2 projection vector")
        payload = {
            "target": request.target.model_dump(mode="json"),
            "vector": list(request.vector),
            "vector_hash": fingerprint(list(request.vector)),
        }
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            previous = tx.read("p2_vector_projections", request.target.vector_id)
            deletion = tx.read("p2_vector_deletions", request.target.vector_id)
            if deletion or (previous and previous["deleted"]):
                tx.abort(ErrorCode.MEMORY_GONE, "P2 projection was deleted")
            if previous and previous["payload"] != payload:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "P2 vector ID payload changed")
            if previous is None:
                tx.write(
                    "p2_vector_projections",
                    request.target.vector_id,
                    {"payload": payload, "operation_id": request.operation_id, "deleted": False},
                )
            tx.before_commit.append(lambda: self.identity.revalidate(tx, ctx))
        if previous is not None:
            # No second insert: current P2 InsertVector appends duplicate IDs.
            return await self.inspect(ctx, request.target, request.operation_id)
        metadata = {
            "p3_projection": {
                "target": payload["target"],
                "schema": SCHEMA,
                "vector_hash": payload["vector_hash"],
                "vector": payload["vector"],
            }
        }
        record = EmbeddingRecord(
            request_id=request.operation_id,
            trace_id=ctx.trace_id,
            source_id=request.target.input_hash,
            object_id=request.target.memory.memory_id,
            chunk_id=request.target.vector_id,
            chunk_text="",
            vector=list(request.vector),
            embedding_model=self.model_space,
            metadata=metadata,
        )
        self.guard(ctx, request.target, Permission.READ)
        try:
            await self.call(ctx, self.client.upsert_vectors, [record])
        except FoundationError:
            raise
        except Exception:
            return self.result(ctx, request.target, request.operation_id, "unknown")
        return await self.inspect(ctx, request.target, request.operation_id)

    async def inspect(
        self, ctx: TrustedContext, target: ProjectionTarget, operation_id: str
    ) -> ProjectionResult:
        self.guard(ctx, target, Permission.READ)
        with self.uow.transaction() as tx:
            row = tx.read("p2_vector_projections", target.vector_id)
            deletion = tx.read("p2_vector_deletions", target.vector_id)
        if deletion:
            return self.result(
                ctx, target, operation_id, "absent" if deletion["confirmed"] else "unknown"
            )
        if row is None:
            return self.result(ctx, target, operation_id, "unknown")
        vector = row["payload"]["vector"]
        try:
            hits = await self.call(ctx, self.client.search_vectors, vector, top_k=self.max_hits)
        except FoundationError:
            raise
        except Exception:
            return self.result(ctx, target, operation_id, "unknown")
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.READ, self.ref(target))
            current = tx.read("p2_vector_projections", target.vector_id)
            if not current or current["payload"] != row["payload"]:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "P2 projection journal changed")
            deletion = tx.read("p2_vector_deletions", target.vector_id)
            matches = [hit for hit in hits if hit.id == target.vector_id]
            if len(hits) > self.max_hits or len(matches) > 1:
                tx.abort(
                    ErrorCode.CONTRACT_VIOLATION, "P2 search bound or vector uniqueness differs"
                )
            if deletion:
                state = "absent" if deletion["confirmed"] else "unknown"
            elif matches:
                expected = {
                    "target": target.model_dump(mode="json"),
                    "schema": SCHEMA,
                    "vector_hash": row["payload"]["vector_hash"],
                    "vector": vector,
                }
                expected_score = 1.0  # Current P2 FlatIndex uses cosine similarity.
                if any(
                    hit.metadata.get("p3_projection") != expected
                    or not math.isfinite(hit.score)
                    or not math.isclose(hit.score, expected_score, rel_tol=1e-4, abs_tol=1e-5)
                    for hit in matches
                ):
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "P2 projection payload evidence differs")
                state = "unknown" if current["deleted"] else "verified"
            else:
                # Search omits frozen/archived segments and is never an exact get.
                state = "unknown"
        return self.result(ctx, target, operation_id, state)

    async def delete(
        self, ctx: TrustedContext, target: ProjectionTarget, operation_id: str
    ) -> ProjectionResult:
        self.guard(ctx, target, Permission.DELETE)
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.DELETE, self.ref(target))
            deletion = tx.read("p2_vector_deletions", target.vector_id)
            if deletion and deletion["confirmed"]:
                return self.result(ctx, target, operation_id, "absent")
            if deletion is None:
                tx.write(
                    "p2_vector_deletions",
                    target.vector_id,
                    {
                        "target": target.model_dump(mode="json"),
                        "operation_id": operation_id,
                        "confirmed": False,
                    },
                )
            row = tx.read("p2_vector_projections", target.vector_id)
            if row:
                row["deleted"] = True
                tx.write("p2_vector_projections", target.vector_id, row)
            tx.before_commit.append(lambda: self.identity.revalidate(tx, ctx))
        try:
            await self.call(ctx, self.client.delete_vectors, [target.vector_id])
        except FoundationError:
            raise
        except Exception:
            return self.result(ctx, target, operation_id, "unknown")
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.DELETE, self.ref(target))
            deletion = tx.read("p2_vector_deletions", target.vector_id)
            deletion["confirmed"] = True
            tx.write("p2_vector_deletions", target.vector_id, deletion)
            tx.before_commit.append(lambda: self.identity.revalidate(tx, ctx))
        # Current P2 acknowledges a WAL-backed, permanent query tombstone,
        # including against late inserts. This is logical removal, not byte erasure.
        return self.result(ctx, target, operation_id, "absent")

    def decode(self, hit: P2VectorHit) -> ProjectionTarget | None:
        envelope = hit.metadata.get("p3_projection")
        if not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA:
            return None
        try:
            return ProjectionTarget.model_validate(envelope["target"])
        except (ValueError, KeyError, TypeError):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "invalid P2 projection metadata"
            ) from None

    async def search(self, ctx: TrustedContext, request: VectorSearchRequest) -> VectorSearchResult:
        ctx = self.request_context(ctx, request.deadline_at)
        select_scope(ctx, request.selection)
        if (
            request.model_space != self.model_space
            or len(request.vector) != self.dimensions
            or not all(math.isfinite(value) for value in request.vector)
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid P2 vector search")
        try:
            hits = await self.call(
                ctx, self.client.search_vectors, request.vector, top_k=self.max_hits
            )
        except FoundationError:
            raise
        except Exception:
            return VectorSearchResult(
                candidates=(), coverage="unavailable", reason="p2_unavailable"
            )
        candidates: dict[str, tuple[float, ProjectionTarget]] = {}
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if len(hits) > self.max_hits or len({hit.id for hit in hits}) != len(hits):
                tx.abort(
                    ErrorCode.CONTRACT_VIOLATION, "P2 search bound or vector uniqueness differs"
                )
            for hit in hits:
                target = self.decode(hit)
                if target is None:
                    continue
                if not math.isfinite(hit.score):
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "nonfinite P2 score")
                if (
                    target.model_space != self.model_space
                    or hit.score <= 0
                    or (request.memory_source and target.memory_source != request.memory_source)
                    or not self.identity.discoverable(tx, ctx, self.ref(target), request.selection)
                ):
                    continue
                self.guard_target(tx, target)
                if hit.id != target.vector_id:
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "P2 vector ID differs from target")
                row = tx.read("p2_vector_projections", hit.id)
                if row and row["deleted"]:
                    continue
                candidates[hit.id] = (hit.score, target)
        ordered = sorted(candidates.items(), key=lambda item: (-item[1][0], item[0]))
        bounded = len(hits) >= self.max_hits
        return VectorSearchResult(
            candidates=tuple(
                VectorCandidate(target=item[1][1], rank=rank + 1, score=item[1][0])
                for rank, item in enumerate(ordered[: request.limit])
            ),
            coverage="partial" if bounded else "complete",
            reason="p2_candidate_bound" if bounded else None,
        )

    def guard_target(self, tx: Any, target: ProjectionTarget) -> None:
        if target != projection_target(
            target.memory,
            target.input_hash,
            target.model_space,
            chunk_index=target.chunk_index,
            generation=target.generation,
            body_hash=target.body_hash,
            memory_source=target.memory_source,
        ):
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "P2 search target identity differs")

    async def health(self, ctx: TrustedContext) -> dict[str, object]:
        await self.call(ctx, self.client.list_segments, engine="vector/default")
        return {"state": "available", "provider": "current_p2", "production_ready": False}

    def generation_search(self) -> CurrentP2GenerationSearch:
        return CurrentP2GenerationSearch(self)

    def close(self) -> None:
        self.available = False


class CurrentP2GenerationSearch:
    def __init__(self, vectors: CurrentP2Vectors) -> None:
        self.vectors = vectors

    async def search(self, ctx: TrustedContext, request: ChunkSearchRequest) -> ChunkSearchResult:
        # Cosine and inner product agree for unit vectors. Keep the caller's
        # model-space binding; never silently normalize a mismatched embedding.
        if (
            request.model_space.metric not in {"cosine", "inner_product"}
            or request.model_space.normalization != "unit"
            or not math.isclose(sum(v * v for v in request.vector), 1.0, abs_tol=1e-4)
        ):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION,
                "current P2 requires unit cosine/inner-product vectors",
            )
        result = await self.vectors.search(
            ctx,
            VectorSearchRequest(
                selection=request.selection,
                memory_source=request.memory_source,
                vector=request.vector,
                model_space=request.model_space.model_space,
                limit=self.vectors.max_hits,
                deadline_at=request.deadline_at,
            ),
        )
        hits = [
            ChunkHit.model_validate(
                {
                    **candidate.target.model_dump(mode="json"),
                    "rank": candidate.rank,
                    "score": candidate.score,
                }
            )
            for candidate in result.candidates
            if candidate.target.generation is not None
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.vector_id))
        values = [
            (f"{rank:012d}", hit.model_copy(update={"rank": rank + 1}).model_dump(mode="json"))
            for rank, hit in enumerate(hits)
        ]
        with self.vectors.uow.transaction() as tx:
            self.vectors.identity.revalidate(tx, ctx)
            binding = [
                ctx.principal.model_dump(mode="json"),
                request.model_dump(mode="json", exclude={"cursor", "limit"}),
                fingerprint(values),
            ]
            selected, cursor = tx.page(
                values, binding, PageRequest(limit=request.limit, cursor=request.cursor)
            )
        return ChunkSearchResult(
            request=request,
            hits=tuple(ChunkHit.model_validate(hit) for hit in selected),
            coverage=result.coverage,
            next_cursor=cursor,
            reason_code=result.reason or "ok",
        )
