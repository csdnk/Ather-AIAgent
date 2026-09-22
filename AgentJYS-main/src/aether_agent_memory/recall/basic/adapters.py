"""Lexical encoding and legacy imports. Projection implementation belongs to Remember."""

import math
import unicodedata
from hashlib import sha256

from aether_agent_memory.recall.contracts.models import (
    EmbeddingItem,
    EmbeddingRequest,
    EmbeddingResult,
)

# Backward-compatible imports; new code imports the owner or Host composition directly.
from aether_agent_memory.remember.basic.projection import projection_target as projection_target
from aether_agent_memory.runtime.contracts.models import ErrorCode, TrustedContext
from aether_agent_memory.runtime.flows.vector_adapters import SQLiteVectors as SQLiteVectors
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.vector_backend import SPACE as SPACE


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
