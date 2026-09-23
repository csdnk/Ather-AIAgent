"""Read-only SQLite generation search; publication belongs to B."""
# SQLite 多块索引只读适配：在授权作用域内计算相似度并提供稳定分页。
# 这里只消费 generation_vectors；索引发布和生产写入由 B 负责。

import math

from aether_agent_memory.recall.contracts.foundation import (
    ChunkHit,
    ChunkSearchRequest,
    ChunkSearchResult,
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
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import matches, select_scope
from aether_agent_memory.runtime.foundation.storage import SQLiteUnitOfWork
from aether_agent_memory.runtime.foundation.telemetry import observed


@observed("recall.chunk_search")
class SQLiteGenerationSearch:
    def __init__(self, uow: SQLiteUnitOfWork, identity: Identity) -> None:
        # 复用 RF 的事务和身份校验，避免查询绕过租户隔离。
        self.uow, self.identity = uow, identity

    async def search(self, ctx: TrustedContext, request: ChunkSearchRequest) -> ChunkSearchResult:
        # 按模型空间与作用域过滤，再排序分页；数据损坏或过期直接失败。
        scope = select_scope(ctx, request.selection)
        space = request.model_space
        if request.deadline_at > ctx.deadline_at or not all(
            math.isfinite(v) for v in request.vector
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid query vector or deadline")
        if self.identity.clock() >= request.deadline_at:
            raise FoundationError(ErrorCode.DEADLINE_EXCEEDED, "chunk search expired")
        with self.uow.transaction() as tx:
            self.identity.authorize(
                tx,
                ctx,
                Permission.READ,
                RecordRef(
                    owner=Flow.RECALL,
                    object_type="search",
                    object_id=request.operation_id,
                    scope=scope,
                ),
            )
            hits = []
            for _, row in tx.rows("generation_vectors"):
                if self.identity.clock() >= request.deadline_at:
                    raise FoundationError(ErrorCode.DEADLINE_EXCEEDED, "chunk scan expired")
                hit = ChunkHit.model_validate(row["hit"])
                if hit.model_space != space.model_space or not matches(hit.memory.scope, scope):
                    continue
                vector = row["vector"]
                if len(vector) != space.dimensions or not all(
                    isinstance(v, (float, int)) and math.isfinite(v) for v in vector
                ):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "corrupt chunk vector")
                dot = sum(a * b for a, b in zip(request.vector, vector, strict=True))
                # 统一采用“分数越大越相似”：L2 距离取负值，余弦显式除以两侧模长。
                if space.metric == "l2":
                    score = -sum((a - b) ** 2 for a, b in zip(request.vector, vector, strict=True))
                elif space.metric == "cosine":
                    norm = math.sqrt(
                        sum(v * v for v in vector) * sum(v * v for v in request.vector)
                    )
                    if not norm:
                        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "zero cosine vector")
                    score = dot / norm
                else:
                    score = dot
                if not math.isfinite(score):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "nonfinite similarity")
                hits.append(hit.model_copy(update={"score": score}))
            # 用记忆引用及 vector_id 打破同分平局，让分页顺序确定。
            hits.sort(key=lambda h: (-h.score, h.memory.model_dump_json(), h.vector_id))
            values = [
                (f"{i:012d}", h.model_copy(update={"rank": i + 1}).model_dump(mode="json"))
                for i, h in enumerate(hits)
            ]
            # 游标绑定调用人、查询和整份结果摘要；索引变化后旧游标不能继续拼页。
            binding = [
                ctx.principal.model_dump(mode="json"),
                request.model_dump(mode="json", exclude={"cursor", "limit"}),
                fingerprint(values),
            ]
            selected, cursor = tx.page(
                values,
                binding,
                PageRequest(
                    limit=request.limit,
                    cursor=request.cursor,
                ),
            )
        if self.identity.clock() >= request.deadline_at:
            raise FoundationError(ErrorCode.DEADLINE_EXCEEDED, "chunk scan expired")
        return ChunkSearchResult(
            request=request,
            hits=tuple(ChunkHit.model_validate(h) for h in selected),
            coverage="complete",
            next_cursor=cursor,
            reason_code="ok",
        )
