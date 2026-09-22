"""Recall-owned read-only vector searches. No projection mutation methods."""

import json
import math

from aether_agent_memory.recall.contracts.models import (
    VectorCandidate,
    VectorSearchRequest,
    VectorSearchResult,
)
from aether_agent_memory.recall.contracts.ports import VectorSearchPort
from aether_agent_memory.remember.contracts.models import ProjectionTarget
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import matches, select_scope
from aether_agent_memory.runtime.vector_backend import MilvusConnection, SQLiteVectorStore


class SQLiteVectorSearch(SQLiteVectorStore):
    async def search(self, ctx: TrustedContext, request: VectorSearchRequest) -> VectorSearchResult:
        self.check(ctx)
        scope = select_scope(ctx, request.selection)
        if (
            request.model_space != self.model_space
            or len(request.vector) != self.dimensions
            or not all(math.isfinite(v) for v in request.vector)
            or request.deadline_at > ctx.deadline_at
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid vector search binding")
        with self.uow.transaction() as tx:
            items = []
            for _, stored in tx.rows("recall_vectors"):
                target = ProjectionTarget.model_validate(stored["target"])
                if target.model_space != request.model_space or not matches(
                    target.memory.scope, scope
                ):
                    continue
                if not self.identity.permits(tx, ctx, Permission.READ, self.ref(target)):
                    continue
                if len(stored["vector"]) != self.dimensions or not all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in stored["vector"]
                ):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "corrupt stored vector")
                score = sum(a * b for a, b in zip(request.vector, stored["vector"], strict=True))
                if score > 0:
                    items.append((score, target.vector_id, target))
        items.sort(key=lambda item: (-item[0], item[1]))
        return VectorSearchResult(
            candidates=tuple(
                VectorCandidate(target=item[2], rank=i + 1, score=item[0])
                for i, item in enumerate(items[: request.limit])
            ),
            coverage="complete",
        )


class MilvusVectorSearch(MilvusConnection):
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
                        tx, ctx, Permission.READ, SQLiteVectorStore.ref(target)
                    )
                ):
                    continue
                score = float(hit["distance"])
                if not math.isfinite(score):
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "nonfinite Milvus score")
                if score > 0:
                    items.append(VectorCandidate(target=target, rank=len(items) + 1, score=score))
        return VectorSearchResult(candidates=tuple(items), coverage="complete")


class SearchAccess:
    """Recall receives search capability without write, inspect or delete methods."""

    def __init__(self, provider: VectorSearchPort) -> None:
        self._provider = provider

    async def search(self, ctx: TrustedContext, request: VectorSearchRequest) -> VectorSearchResult:
        return await self._provider.search(ctx, request)
