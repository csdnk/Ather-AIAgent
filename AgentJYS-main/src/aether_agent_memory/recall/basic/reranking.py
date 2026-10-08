"""Bounded CrossEncoder inference. Timed-out work retains its physical CPU slot."""

import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from typing import Any, Protocol

from aether_agent_memory.runtime.contracts.models import ErrorCode, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError


class Reranker(Protocol):
    identifier: str

    async def rerank(
        self, ctx: TrustedContext, query: str, documents: tuple[str, ...]
    ) -> tuple[float, ...]: ...


class CrossEncoderReranker:
    def __init__(
        self,
        model: str,
        *,
        revision: str | None = None,
        cache: str | None = None,
        max_length: int = 512,
    ) -> None:
        self.identifier = "cross_encoder:" + model + "@" + (revision or "default")
        self.model_name, self.revision, self.cache = model, revision, cache
        self.max_length = max_length
        self.model: Any = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="p3-rerank")
        self.slot = BoundedSemaphore(1)
        self.closed = False

    def compute(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        if self.model is None:
            from sentence_transformers import CrossEncoder

            self.model = CrossEncoder(
                self.model_name,
                revision=self.revision,
                cache_folder=self.cache,
                device="cpu",
                max_length=self.max_length,
                trust_remote_code=False,
            )
        pairs = [(query, document) for document in documents]
        encoded = self.model.tokenizer(pairs, truncation=False, padding=False)
        if any(len(tokens) > self.max_length for tokens in encoded["input_ids"]):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "reranker pair exceeds token limit")
        result = self.model.predict(pairs, batch_size=8, show_progress_bar=False)
        try:
            scores = tuple(float(score) for score in result)
        except (TypeError, ValueError, OverflowError) as exc:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid reranker scores") from exc
        if len(scores) != len(documents) or not all(math.isfinite(score) for score in scores):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid reranker scores")
        return scores

    async def rerank(
        self, ctx: TrustedContext, query: str, documents: tuple[str, ...]
    ) -> tuple[float, ...]:
        if self.closed or not self.slot.acquire(blocking=False):
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "reranker unavailable or busy")
        try:
            pending = self.executor.submit(self.compute, query, documents)
        except BaseException:
            self.slot.release()
            raise
        pending.add_done_callback(lambda _: self.slot.release())
        # Shield prevents cancellation from pretending the CPU worker has stopped.
        wrapped = asyncio.wrap_future(pending)
        wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        try:
            return await asyncio.shield(wrapped)
        except FoundationError:
            raise
        except Exception as exc:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "reranker inference failed"
            ) from exc

    async def health(self, ctx: TrustedContext) -> dict[str, object]:
        return {
            "state": "unavailable" if self.closed else "available" if self.model else "unknown",
            "model_id": self.identifier,
            "loaded": self.model is not None,
        }

    def close(self) -> None:
        self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
