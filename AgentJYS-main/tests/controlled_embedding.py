"""Deterministic vectors for fault/contract tests; real model gates use NativeP3Embedding."""

import math
import unicodedata
from hashlib import sha256

from aether_agent_memory.recall.contracts.foundation import EmbeddingSpace
from aether_agent_memory.recall.contracts.models import (
    EmbeddingItem,
    EmbeddingRequest,
    EmbeddingResult,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash

TEST_SPACE = "contract_test_bigrams_v1"


class ControlledEmbedding:
    model_space = TEST_SPACE
    dimensions = 256
    space = EmbeddingSpace(
        model_space=TEST_SPACE,
        model_id="controlled_contract_embedding",
        model_revision="v1",
        dimensions=256,
        tokenizer_id="cl100k_base",
        query_prefix="",
        passage_prefix="",
        normalization="unit",
        metric="inner_product",
        max_input_tokens=512,
    )

    @staticmethod
    def features(text):
        clean = "".join(unicodedata.normalize("NFKC", text).lower().split())
        tokens = list(clean) + [clean[i : i + 2] for i in range(len(clean) - 1)]
        vector = [0.0] * 256
        for token in tokens:
            vector[int.from_bytes(sha256(token.encode()).digest()[:2], "big") % 256] += 1
        norm = math.sqrt(sum(v * v for v in vector)) or 1
        return tuple(v / norm for v in vector)

    async def embed(self, ctx, request):
        if request.model_space != self.model_space or request.deadline_at > ctx.deadline_at:
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "test embedding space or deadline differs"
            )
        return EmbeddingResult(
            operation_id=request.operation_id,
            usage=request.usage,
            model_space=self.model_space,
            dimensions=self.dimensions,
            items=tuple(
                EmbeddingItem(index=i, input_hash=text_hash(text), vector=self.features(text))
                for i, text in enumerate(request.texts)
            ),
        )

    async def health(self, ctx):
        result = await self.embed(
            ctx,
            EmbeddingRequest(
                operation_id="controlled_health_probe",
                usage="query",
                texts=("health probe",),
                model_space=self.model_space,
                deadline_at=ctx.deadline_at,
            ),
        )
        vector = result.items[0].vector
        return {
            "state": "available"
            if len(vector) == self.dimensions and all(math.isfinite(v) for v in vector)
            else "unavailable"
        }
