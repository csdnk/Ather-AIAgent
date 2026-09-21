"""Explicit local lexical baseline; this is not a semantic model or Milvus adapter."""

import math
import unicodedata
from hashlib import sha256

from aether_agent_memory.recall.contracts.models import (
    EmbeddingItem,
    EmbeddingRequest,
    EmbeddingResult,
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
    VectorCandidate,
    VectorSearchRequest,
    VectorSearchResult,
)
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import matches, select_scope, text_hash
from aether_agent_memory.runtime.foundation.storage import SQLiteUnitOfWork

SPACE = "local_lexical_bigrams_v1"


class LexicalEmbedding:
    model_space = SPACE
    dimensions = 256

    @staticmethod
    def features(text: str) -> tuple[float, ...]:
        clean = "".join(unicodedata.normalize("NFKC", text).lower().split())
        tokens = list(clean) + [clean[i : i + 2] for i in range(len(clean) - 1)]
        vector = [0.0] * 256
        for token in tokens:
            vector[int.from_bytes(sha256(token.encode()).digest()[:2], "big") % 256] += 1
        norm = math.sqrt(sum(v * v for v in vector)) or 1
        return tuple(v / norm for v in vector)

    async def embed(self, ctx: TrustedContext, request: EmbeddingRequest) -> EmbeddingResult:
        if request.model_space != SPACE or request.deadline_at > ctx.deadline_at:
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "unsupported lexical model space or deadline"
            )
        return EmbeddingResult(
            operation_id=request.operation_id,
            usage=request.usage,
            model_space=SPACE,
            dimensions=256,
            items=tuple(
                EmbeddingItem(index=i, input_hash=text_hash(text), vector=self.features(text))
                for i, text in enumerate(request.texts)
            ),
        )


class SQLiteVectors:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        model_space: str = SPACE,
        dimensions: int = 256,
    ) -> None:
        self.uow, self.identity = uow, identity
        self.model_space, self.dimensions = model_space, dimensions
        self.available = True

    def check(self, ctx: TrustedContext) -> None:
        if not self.available:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "local vector adapter unavailable"
            )
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)

    @staticmethod
    def ref(target: ProjectionTarget) -> RecordRef:
        return RecordRef(
            owner=Flow.REMEMBER,
            object_type="memory",
            object_id=target.memory.memory_id,
            scope=target.memory.scope,
        )

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
