"""Cross-request dynamic batching for the B1 embedding sidecar."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

import numpy as np

InputType = Literal["passage", "query"]
EmbeddingExecutor = Callable[[list[str], list[str]], Awaitable[list[np.ndarray]]]


class DynamicBatchError(RuntimeError):
    """Base class for dynamic batching failures."""


class DynamicBatchClosed(DynamicBatchError):  # noqa: N818
    """Raised when a job is submitted after scheduler shutdown starts."""


class DynamicBatchQueueFull(DynamicBatchError):  # noqa: N818
    """Raised when the bounded dynamic batch queue is full."""


class DynamicBatchTimeout(TimeoutError):  # noqa: N818
    """Raised when a job waits too long before inference starts."""


@dataclass(frozen=True)
class BatchPolicy:
    max_batch_items: int = 64
    max_batch_tokens: int = 8192
    max_wait_ms: float = 2.0
    queue_size: int = 4096
    length_buckets: tuple[int, ...] = (32, 64, 128, 256, 512)
    backend_timeout_seconds: float = 120.0
    shutdown_drain_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.max_batch_items <= 0:
            raise ValueError("max_batch_items must be positive")
        if self.max_batch_tokens <= 0:
            raise ValueError("max_batch_tokens must be positive")
        if self.max_wait_ms <= 0:
            raise ValueError("max_wait_ms must be positive")
        if self.queue_size <= 0:
            raise ValueError("queue_size must be positive")
        if self.backend_timeout_seconds <= 0:
            raise ValueError("backend_timeout_seconds must be positive")
        if self.shutdown_drain_timeout_seconds <= 0:
            raise ValueError("shutdown_drain_timeout_seconds must be positive")
        if not self.length_buckets or any(value <= 0 for value in self.length_buckets):
            raise ValueError("length_buckets must contain positive values")
        if tuple(sorted(self.length_buckets)) != self.length_buckets:
            raise ValueError("length_buckets must be sorted")


@dataclass
class PendingEmbeddingJob:
    text: str
    input_type: InputType
    request_id: str
    trace_id: str | None
    enqueue_time: float
    deadline: float
    estimated_tokens: int
    future: asyncio.Future[EmbeddingJobResult]


@dataclass(frozen=True)
class EmbeddingJobResult:
    vector: np.ndarray
    queue_wait_ms: float
    backend_inference_ms: float
    batch_size: int
    batch_tokens: int
    padding_efficiency: float


@dataclass(frozen=True)
class BatchRun:
    jobs: list[PendingEmbeddingJob]
    input_type: InputType
    bucket: int
    token_count: int


def estimate_tokens(text: str, *, max_length: int = 512) -> int:
    """Return a conservative dependency-free token estimate for bucketing."""

    stripped = text.strip()
    if not stripped:
        return 1
    cjk = sum(1 for char in stripped if "\u4e00" <= char <= "\u9fff")
    asciiish = max(len(stripped) - cjk, 0)
    estimate = cjk + math.ceil(asciiish / 4)
    return max(1, min(max_length, estimate))


class DynamicBatchMetrics:
    def __init__(self, *, queue_capacity: int, window_size: int = 4096) -> None:
        self.queue_capacity = queue_capacity
        self.submitted_items = 0
        self.completed_items = 0
        self.dropped_items = 0
        self.batch_count = 0
        self.fallback_count = 0
        self.queue_timeout_count = 0
        self.backend_timeout_count = 0
        self.backend_error_count = 0
        self._batch_items: deque[float] = deque(maxlen=window_size)
        self._batch_tokens: deque[float] = deque(maxlen=window_size)
        self._queue_wait_ms: deque[float] = deque(maxlen=window_size)
        self._backend_ms: deque[float] = deque(maxlen=window_size)
        self._padding_efficiency: deque[float] = deque(maxlen=window_size)

    @staticmethod
    def _percentile(values: deque[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        remainder = position - lower
        return ordered[lower] * (1 - remainder) + ordered[upper] * remainder

    @staticmethod
    def _average(values: deque[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def record_batch(
        self,
        *,
        item_count: int,
        token_count: int,
        queue_waits_ms: list[float],
        backend_ms: float,
        padding_efficiency: float,
    ) -> None:
        self.batch_count += 1
        self.completed_items += item_count
        self._batch_items.append(float(item_count))
        self._batch_tokens.append(float(token_count))
        self._backend_ms.append(float(backend_ms))
        self._padding_efficiency.append(float(padding_efficiency))
        self._queue_wait_ms.extend(float(value) for value in queue_waits_ms)

    def snapshot(self, *, queue_depth: int, running: bool) -> dict[str, float | int | bool]:
        return {
            "dynamic_batch_enabled": running,
            "queue_depth": queue_depth,
            "queue_capacity": self.queue_capacity,
            "submitted_items": self.submitted_items,
            "completed_items": self.completed_items,
            "dropped_items": self.dropped_items,
            "batch_count": self.batch_count,
            "batch_items_avg": round(self._average(self._batch_items), 3),
            "batch_items_p50": round(self._percentile(self._batch_items, 0.50), 3),
            "batch_items_p95": round(self._percentile(self._batch_items, 0.95), 3),
            "batch_items_max": round(max(self._batch_items) if self._batch_items else 0.0, 3),
            "batch_tokens_avg": round(self._average(self._batch_tokens), 3),
            "batch_tokens_p95": round(self._percentile(self._batch_tokens, 0.95), 3),
            "queue_wait_p50_ms": round(self._percentile(self._queue_wait_ms, 0.50), 3),
            "queue_wait_p95_ms": round(self._percentile(self._queue_wait_ms, 0.95), 3),
            "queue_wait_p99_ms": round(self._percentile(self._queue_wait_ms, 0.99), 3),
            "backend_inference_p50_ms": round(self._percentile(self._backend_ms, 0.50), 3),
            "backend_inference_p95_ms": round(self._percentile(self._backend_ms, 0.95), 3),
            "backend_inference_p99_ms": round(self._percentile(self._backend_ms, 0.99), 3),
            "padding_efficiency": round(self._average(self._padding_efficiency), 6),
            "fallback_count": self.fallback_count,
            "queue_timeout_count": self.queue_timeout_count,
            "backend_timeout_count": self.backend_timeout_count,
            "backend_error_count": self.backend_error_count,
        }


class DynamicBatchScheduler:
    """Length-aware, deadline-aware cross-request embedding batcher."""

    def __init__(
        self,
        policy: BatchPolicy,
        executor: EmbeddingExecutor,
    ) -> None:
        self.policy = policy
        self._executor = executor
        self._queue: asyncio.Queue[PendingEmbeddingJob | None] = asyncio.Queue(
            maxsize=policy.queue_size
        )
        self._pending: list[PendingEmbeddingJob] = []
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self.metrics = DynamicBatchMetrics(queue_capacity=policy.queue_size)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done() and not self._closing

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize() + len(self._pending)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._closing = False
            self._task = asyncio.create_task(self._run(), name="b1-dynamic-batch-scheduler")

    async def close(self) -> None:
        self._closing = True
        if self._task is None:
            self._fail_pending(DynamicBatchClosed("dynamic batch scheduler is closed"))
            return
        with suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        try:
            await asyncio.wait_for(self._task, timeout=self.policy.shutdown_drain_timeout_seconds)
        except TimeoutError:
            self._task.cancel()
            self._fail_pending(DynamicBatchClosed("dynamic batch scheduler shutdown timed out"))
            await asyncio.gather(self._task, return_exceptions=True)

    def snapshot(self) -> dict[str, float | int | bool]:
        return self.metrics.snapshot(queue_depth=self.queue_depth, running=self.running)

    def submit(
        self,
        *,
        text: str,
        input_type: InputType,
        request_id: str,
        trace_id: str | None,
        estimated_tokens: int,
        timeout_seconds: float,
    ) -> asyncio.Future[EmbeddingJobResult]:
        if self._closing:
            raise DynamicBatchClosed("dynamic batch scheduler is shutting down")
        if self._task is None or self._task.done():
            raise DynamicBatchClosed("dynamic batch scheduler is not running")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[EmbeddingJobResult] = loop.create_future()
        now = time.perf_counter()
        job = PendingEmbeddingJob(
            text=text,
            input_type=input_type,
            request_id=request_id,
            trace_id=trace_id,
            enqueue_time=now,
            deadline=now + timeout_seconds,
            estimated_tokens=max(1, estimated_tokens),
            future=future,
        )
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull as exc:
            self.metrics.dropped_items += 1
            raise DynamicBatchQueueFull("dynamic batch queue is full") from exc
        self.metrics.submitted_items += 1
        return future

    async def _run(self) -> None:
        try:
            while not self._closing or self._pending or not self._queue.empty():
                await self._receive_until_ready()
                self._drop_cancelled_or_expired()
                batch = self._select_batch()
                if batch is None:
                    continue
                await self._execute(batch)
        except asyncio.CancelledError:
            self._fail_pending(DynamicBatchClosed("dynamic batch scheduler was cancelled"))
            raise
        except Exception as exc:
            self._fail_pending(exc)
            raise
        finally:
            self._fail_pending(DynamicBatchClosed("dynamic batch scheduler stopped"))

    async def _receive_until_ready(self) -> None:
        while True:
            self._drain_ready_items()
            if self._ready_batch_available():
                return
            timeout = self._seconds_until_oldest_deadline() if self._pending else None
            try:
                if timeout is None:
                    item = await self._queue.get()
                else:
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except TimeoutError:
                return
            if item is None:
                self._closing = True
                return
            self._pending.append(item)

    def _drain_ready_items(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is None:
                self._closing = True
                return
            self._pending.append(item)

    def _seconds_until_oldest_deadline(self) -> float:
        now = time.perf_counter()
        oldest = min(job.enqueue_time for job in self._pending)
        max_wait_deadline = oldest + self.policy.max_wait_ms / 1000.0
        explicit_deadline = min(job.deadline for job in self._pending)
        return max(0.0, min(max_wait_deadline, explicit_deadline) - now)

    def _bucket_for(self, tokens: int) -> int:
        for bucket in self.policy.length_buckets:
            if tokens <= bucket:
                return bucket
        return self.policy.length_buckets[-1]

    def _ready_batch_available(self) -> bool:
        if not self._pending:
            return False
        now = time.perf_counter()
        groups: dict[tuple[InputType, int], list[PendingEmbeddingJob]] = {}
        for job in self._pending:
            if not job.future.cancelled():
                groups.setdefault(
                    (job.input_type, self._bucket_for(job.estimated_tokens)), []
                ).append(job)
        if not groups:
            return False
        oldest_job = min(
            (job for jobs in groups.values() for job in jobs),
            key=lambda item: item.enqueue_time,
        )
        if now - oldest_job.enqueue_time >= self.policy.max_wait_ms / 1000.0:
            return True
        return any(
            len(jobs) >= self.policy.max_batch_items
            or sum(job.estimated_tokens for job in jobs) >= self.policy.max_batch_tokens
            for jobs in groups.values()
        )

    def _select_batch(self) -> BatchRun | None:
        if not self._pending:
            return None
        now = time.perf_counter()
        groups: dict[tuple[InputType, int], list[PendingEmbeddingJob]] = {}
        for job in self._pending:
            if job.future.cancelled():
                continue
            groups.setdefault(
                (job.input_type, self._bucket_for(job.estimated_tokens)), []
            ).append(job)
        if not groups:
            return None

        forced_key: tuple[InputType, int] | None = None
        oldest_job = min(
            (job for jobs in groups.values() for job in jobs),
            key=lambda item: item.enqueue_time,
        )
        if now - oldest_job.enqueue_time >= self.policy.max_wait_ms / 1000.0:
            forced_key = (oldest_job.input_type, self._bucket_for(oldest_job.estimated_tokens))

        selected_key: tuple[InputType, int] | None = forced_key
        if selected_key is None:
            for key, jobs in groups.items():
                token_sum = sum(job.estimated_tokens for job in jobs)
                if (
                    len(jobs) >= self.policy.max_batch_items
                    or token_sum >= self.policy.max_batch_tokens
                ):
                    selected_key = key
                    break
        if selected_key is None:
            return None

        candidates = sorted(groups[selected_key], key=lambda job: job.enqueue_time)
        selected: list[PendingEmbeddingJob] = []
        token_count = 0
        for job in candidates:
            if len(selected) >= self.policy.max_batch_items:
                break
            if selected and token_count + job.estimated_tokens > self.policy.max_batch_tokens:
                break
            selected.append(job)
            token_count += job.estimated_tokens
        if not selected:
            return None
        selected_ids = {id(job) for job in selected}
        self._pending = [job for job in self._pending if id(job) not in selected_ids]
        return BatchRun(
            jobs=selected,
            input_type=selected_key[0],
            bucket=selected_key[1],
            token_count=token_count,
        )

    def _drop_cancelled_or_expired(self) -> None:
        now = time.perf_counter()
        kept: list[PendingEmbeddingJob] = []
        for job in self._pending:
            if job.future.cancelled():
                self.metrics.dropped_items += 1
                continue
            if now > job.deadline:
                self.metrics.queue_timeout_count += 1
                if not job.future.done():
                    job.future.set_exception(
                        DynamicBatchTimeout("dynamic batch queue wait timed out")
                    )
                continue
            kept.append(job)
        self._pending = kept

    async def _execute(self, batch: BatchRun) -> None:
        active_jobs = [job for job in batch.jobs if not job.future.cancelled()]
        if not active_jobs:
            self.metrics.dropped_items += len(batch.jobs)
            return
        texts = [job.text for job in active_jobs]
        input_types: list[str] = [job.input_type for job in active_jobs]
        started = time.perf_counter()
        queue_waits = [(started - job.enqueue_time) * 1000 for job in active_jobs]
        try:
            vectors = await asyncio.wait_for(
                self._executor(texts, input_types),
                timeout=self.policy.backend_timeout_seconds,
            )
        except asyncio.CancelledError:
            closed_error = DynamicBatchClosed("dynamic batch scheduler stopped during inference")
            for job in active_jobs:
                if not job.future.done():
                    job.future.set_exception(closed_error)
            raise
        except TimeoutError as exc:
            self.metrics.backend_timeout_count += len(active_jobs)
            for job in active_jobs:
                if not job.future.done():
                    job.future.set_exception(exc)
            return
        except Exception as exc:
            self.metrics.backend_error_count += len(active_jobs)
            for job in active_jobs:
                if not job.future.done():
                    job.future.set_exception(exc)
            return
        backend_ms = (time.perf_counter() - started) * 1000
        if len(vectors) != len(active_jobs):
            result_error = ValueError("dynamic batch executor returned an incomplete result")
            self.metrics.backend_error_count += len(active_jobs)
            for job in active_jobs:
                if not job.future.done():
                    job.future.set_exception(result_error)
            return
        padding_efficiency = (
            batch.token_count / (len(active_jobs) * batch.bucket)
            if active_jobs and batch.bucket > 0
            else 1.0
        )
        self.metrics.record_batch(
            item_count=len(active_jobs),
            token_count=batch.token_count,
            queue_waits_ms=queue_waits,
            backend_ms=backend_ms,
            padding_efficiency=padding_efficiency,
        )
        for job, vector, queue_wait_ms in zip(active_jobs, vectors, queue_waits, strict=True):
            if not job.future.done():
                job.future.set_result(
                    EmbeddingJobResult(
                        vector=np.asarray(vector, dtype=np.float32),
                        queue_wait_ms=queue_wait_ms,
                        backend_inference_ms=backend_ms,
                        batch_size=len(active_jobs),
                        batch_tokens=batch.token_count,
                        padding_efficiency=padding_efficiency,
                    )
                )

    def _fail_pending(self, exc: BaseException) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None and not item.future.done():
                item.future.set_exception(exc)
        for job in self._pending:
            if not job.future.done():
                job.future.set_exception(exc)
        self._pending.clear()
