from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pytest
from tests.unit.test_b1_sidecar import FakeBackend, item, settings

from aether_agent_memory.b1.batching import (
    BatchPolicy,
    DynamicBatchClosed,
    DynamicBatchQueueFull,
    DynamicBatchScheduler,
)
from aether_agent_memory.b1.sidecar import B1Service, SidecarSettings


class RecordingExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[list[str], list[str]]] = []
        self.fail = fail

    async def __call__(self, texts: list[str], input_types: list[str]) -> list[np.ndarray]:
        self.calls.append((list(texts), list(input_types)))
        if self.fail:
            raise RuntimeError("injected batch failure")
        await asyncio.sleep(0)
        return [
            np.asarray([index + 1, len(text), len(input_type), 1.0], dtype=np.float32)
            for index, (text, input_type) in enumerate(zip(texts, input_types, strict=True))
        ]


def policy(**overrides: Any) -> BatchPolicy:
    values: dict[str, Any] = {
        "max_batch_items": 2,
        "max_batch_tokens": 128,
        "max_wait_ms": 5.0,
        "queue_size": 16,
        "length_buckets": (4, 8, 16, 512),
        "backend_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return BatchPolicy(**values)


@pytest.mark.unit
async def test_scheduler_batches_across_submitters_by_max_items() -> None:
    executor = RecordingExecutor()
    scheduler = DynamicBatchScheduler(policy(max_wait_ms=50.0), executor)
    scheduler.start()
    try:
        first = scheduler.submit(
            text="alpha",
            input_type="passage",
            request_id="r1",
            trace_id="t1",
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
        second = scheduler.submit(
            text="beta",
            input_type="passage",
            request_id="r2",
            trace_id="t2",
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
        results = await asyncio.gather(first, second)
    finally:
        await scheduler.close()

    assert [result.batch_size for result in results] == [2, 2]
    assert executor.calls == [(["alpha", "beta"], ["passage", "passage"])]


@pytest.mark.unit
async def test_scheduler_flushes_on_token_limit_and_preserves_order() -> None:
    executor = RecordingExecutor()
    scheduler = DynamicBatchScheduler(policy(max_batch_items=8, max_batch_tokens=4), executor)
    scheduler.start()
    try:
        futures = [
            scheduler.submit(
                text=f"text-{index}",
                input_type="query",
                request_id=f"r{index}",
                trace_id=None,
                estimated_tokens=2,
                timeout_seconds=1.0,
            )
            for index in range(2)
        ]
        results = await asyncio.gather(*futures)
    finally:
        await scheduler.close()

    assert [float(result.vector[0]) for result in results] == [1.0, 2.0]
    assert executor.calls == [(["text-0", "text-1"], ["query", "query"])]


@pytest.mark.unit
async def test_scheduler_flushes_single_job_on_max_wait() -> None:
    executor = RecordingExecutor()
    scheduler = DynamicBatchScheduler(policy(max_wait_ms=1.0), executor)
    scheduler.start()
    try:
        result = await scheduler.submit(
            text="lonely",
            input_type="passage",
            request_id="r1",
            trace_id=None,
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
    finally:
        await scheduler.close()

    assert result.batch_size == 1
    assert executor.calls == [(["lonely"], ["passage"])]


@pytest.mark.unit
async def test_scheduler_single_request_can_process_immediately_by_item_limit() -> None:
    executor = RecordingExecutor()
    scheduler = DynamicBatchScheduler(policy(max_batch_items=1, max_wait_ms=50.0), executor)
    scheduler.start()
    try:
        result = await scheduler.submit(
            text="immediate",
            input_type="passage",
            request_id="r1",
            trace_id=None,
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
    finally:
        await scheduler.close()

    assert result.batch_size == 1
    assert executor.calls == [(["immediate"], ["passage"])]


@pytest.mark.unit
async def test_scheduler_uses_length_and_input_type_buckets_without_starvation() -> None:
    executor = RecordingExecutor()
    scheduler = DynamicBatchScheduler(policy(max_batch_items=4, max_wait_ms=1.0), executor)
    scheduler.start()
    try:
        short = scheduler.submit(
            text="s",
            input_type="passage",
            request_id="short",
            trace_id=None,
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
        long = scheduler.submit(
            text="l" * 100,
            input_type="passage",
            request_id="long",
            trace_id=None,
            estimated_tokens=100,
            timeout_seconds=1.0,
        )
        query = scheduler.submit(
            text="q",
            input_type="query",
            request_id="query",
            trace_id=None,
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
        await asyncio.gather(short, long, query)
    finally:
        await scheduler.close()

    assert len(executor.calls) == 3
    assert all(len(set(input_types)) == 1 for _, input_types in executor.calls)
    first_characters = sorted(texts[0][0] for texts, _ in executor.calls)
    assert first_characters == ["l", "q", "s"]


@pytest.mark.unit
async def test_scheduler_queue_full_and_backend_exception_unblock_futures() -> None:
    scheduler = DynamicBatchScheduler(policy(queue_size=1), RecordingExecutor())
    scheduler.start()
    try:
        scheduler.submit(
            text="first",
            input_type="passage",
            request_id="r1",
            trace_id=None,
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
        with pytest.raises(DynamicBatchQueueFull):
            scheduler.submit(
                text="second",
                input_type="passage",
                request_id="r2",
                trace_id=None,
                estimated_tokens=2,
                timeout_seconds=1.0,
            )
    finally:
        await scheduler.close()

    failing = DynamicBatchScheduler(policy(), RecordingExecutor(fail=True))
    failing.start()
    try:
        future = failing.submit(
            text="boom",
            input_type="passage",
            request_id="r3",
            trace_id=None,
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
        with pytest.raises(RuntimeError, match="injected batch failure"):
            await future
    finally:
        await failing.close()


@pytest.mark.unit
async def test_scheduler_timeout_cancellation_and_shutdown_do_not_leave_futures_hanging() -> None:
    async def slow_executor(texts: list[str], input_types: list[str]) -> list[np.ndarray]:
        del texts, input_types
        await asyncio.sleep(1.0)
        return []

    timeout_scheduler = DynamicBatchScheduler(
        policy(max_batch_items=1, backend_timeout_seconds=0.01),
        slow_executor,
    )
    timeout_scheduler.start()
    try:
        future = timeout_scheduler.submit(
            text="timeout",
            input_type="passage",
            request_id="timeout",
            trace_id=None,
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
        with pytest.raises(TimeoutError):
            await future
    finally:
        await timeout_scheduler.close()
    assert timeout_scheduler.snapshot()["backend_timeout_count"] == 1

    cancel_scheduler = DynamicBatchScheduler(policy(max_wait_ms=50.0), RecordingExecutor())
    cancel_scheduler.start()
    try:
        cancelled = cancel_scheduler.submit(
            text="cancelled",
            input_type="passage",
            request_id="cancelled",
            trace_id=None,
            estimated_tokens=2,
            timeout_seconds=1.0,
        )
        cancelled.cancel()
    finally:
        await cancel_scheduler.close()
    assert cancelled.cancelled()
    assert cancel_scheduler.queue_depth == 0
    assert cancel_scheduler.snapshot()["dropped_items"] >= 1

    executor_started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_executor(texts: list[str], input_types: list[str]) -> list[np.ndarray]:
        del input_types
        executor_started.set()
        await release.wait()
        return [np.asarray([1.0], dtype=np.float32) for _ in texts]

    shutdown_scheduler = DynamicBatchScheduler(
        policy(
            max_batch_items=1,
            backend_timeout_seconds=10.0,
            shutdown_drain_timeout_seconds=0.01,
        ),
        blocked_executor,
    )
    shutdown_scheduler.start()
    shutdown_future = shutdown_scheduler.submit(
        text="shutdown",
        input_type="passage",
        request_id="shutdown",
        trace_id=None,
        estimated_tokens=2,
        timeout_seconds=1.0,
    )
    await executor_started.wait()
    await shutdown_scheduler.close()
    with pytest.raises(DynamicBatchClosed):
        await shutdown_future


@pytest.mark.unit
async def test_sidecar_dynamic_batches_concurrent_requests_and_keeps_input_types() -> None:
    backend = FakeBackend(delay=0.01)
    config: SidecarSettings = settings(
        dynamic_batching=True,
        dynamic_max_batch_items=2,
        dynamic_max_wait_ms=50.0,
        dynamic_queue_size=16,
        eager_load=False,
    )
    service = B1Service(config, backend)
    service.load()
    await service.start()
    try:
        first, second = await asyncio.gather(
            service.process(item(request_id="dyn-1", input_type="query")),
            service.process(item(request_id="dyn-2", input_type="query")),
        )
    finally:
        await service.shutdown()

    assert first[1] == second[1] == 200
    assert backend.calls == 1
    assert first[0]["results"][0]["status"] == "success"
    assert second[0]["results"][0]["status"] == "success"
    metrics = service.metrics_snapshot()["dynamic_batch"]
    assert metrics["completed_items"] == 2
    assert metrics["batch_items_max"] == 2
