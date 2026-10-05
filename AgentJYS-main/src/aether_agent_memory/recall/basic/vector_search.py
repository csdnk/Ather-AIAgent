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
from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ErrorCode,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import select_scope
from aether_agent_memory.runtime.vector_backend import MilvusConnection, projection_ref


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
        own = [
            f"{field} == {json.dumps(value, ensure_ascii=True)}"
            for field, value in scope.model_dump().items()
            if value is not None
        ]
        alternatives = ["(" + " and ".join(own) + ")"]

        def shared_filters() -> list[str]:
            filters = []
            with self.uow.transaction() as tx:
                self.identity.revalidate(tx, ctx)
                for raw in tx.read("settings", "grants") or []:
                    grant = AuthorizationGrant.model_validate(raw)
                    if grant.resource.object_type != "memory" or not self.identity.discoverable(
                        tx, ctx, grant.resource, request.selection
                    ):
                        continue
                    fields = [
                        f"{k} == {json.dumps(v, ensure_ascii=True)}"
                        for k, v in grant.resource.scope.model_dump().items()
                        if v is not None
                    ]
                    fields.append(
                        'target["memory"]["memory_id"] == ' + json.dumps(grant.resource.object_id)
                    )
                    filters.append("(" + " and ".join(fields) + ")")
            return filters

        alternatives.extend(await self.metadata(shared_filters))
        conditions = [
            "(" + " or ".join(alternatives) + ")",
            "model_space == " + json.dumps(self.model_space),
        ]
        # 标签保存在原有 JSON target；历史未标记记录只属于 long_term。
        # 在 Milvus Top K 之前过滤，避免其他来源占满候选预算。
        if request.memory_source == "working":
            conditions.append('target["memory_source"] == "working"')
        elif request.memory_source == "long_term":
            conditions.append(
                '(not exists target["memory_source"] or target["memory_source"] == "long_term")'
            )
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

        def candidates() -> tuple[VectorCandidate, ...]:
            items: list[VectorCandidate] = []
            with self.uow.transaction() as tx:
                self.identity.revalidate(tx, ctx)
                for hit in hits[0] if hits else []:
                    target = ProjectionTarget.model_validate(hit["entity"]["target"])
                    if (
                        (
                            request.memory_source is not None
                            and target.memory_source != request.memory_source
                        )
                        or target.model_space != self.model_space
                        or not self.identity.discoverable(
                            tx, ctx, projection_ref(target), request.selection
                        )
                    ):
                        continue
                    score = float(hit["distance"])
                    if not math.isfinite(score):
                        tx.abort(ErrorCode.CONTRACT_VIOLATION, "nonfinite Milvus score")
                    if score > 0:
                        items.append(
                            VectorCandidate(target=target, rank=len(items) + 1, score=score)
                        )
            return tuple(items)

        return VectorSearchResult(candidates=await self.metadata(candidates), coverage="complete")


class SearchAccess:
    """Recall receives search capability without write, inspect or delete methods."""

    def __init__(self, provider: VectorSearchPort) -> None:
        self._provider = provider

    async def search(self, ctx: TrustedContext, request: VectorSearchRequest) -> VectorSearchResult:
        return await self._provider.search(ctx, request)
