"""Adaptive concurrency control for bursty memory writes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter


class AdaptiveConcurrency:
    def __init__(self, *, initial: int = 8, minimum: int = 1, maximum: int = 128, target_latency_ms: float = 10.0, sample_window: int = 16) -> None:
        self._minimum = minimum
        self._maximum = max(maximum, minimum)
        self._limit = min(max(initial, minimum), self._maximum)
        self._target = target_latency_ms
        self._window = max(1, sample_window)
        self._active = 0
        self._samples: list[float] = []
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        started = perf_counter()
        async with self._condition:
            await self._condition.wait_for(lambda: self._active < self._limit)
            self._active += 1
        try:
            yield
        finally:
            elapsed_ms = (perf_counter() - started) * 1000
            async with self._condition:
                self._active -= 1
                self._samples.append(elapsed_ms)
                if len(self._samples) >= self._window:
                    average = sum(self._samples) / len(self._samples)
                    if average < self._target * 0.8:
                        self._limit = min(self._limit + 1, self._maximum)
                    elif average > self._target * 1.2:
                        self._limit = max(self._limit - 1, self._minimum)
                    self._samples.clear()
                self._condition.notify_all()

    @property
    def limit(self) -> int:
        return self._limit
