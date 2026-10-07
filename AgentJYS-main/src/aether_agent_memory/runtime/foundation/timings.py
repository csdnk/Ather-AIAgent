"""Bounded, inclusive stage timings owned by one observed node.

Nested nodes replace the active collector and restore their parent on exit.
Stages capture their owner on entry; a parent stage spanning a child includes
the child's wall time, but child stage samples never roll up to the parent.
Durations therefore overlap and must not be summed into a node total.
ContextVar propagation works with asyncio.to_thread; other executors must
explicitly copy the context. A terminal snapshot closes the collector so a
cancelled worker cannot add late samples. Counts mean exited scopes, not success.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from threading import Lock
from time import perf_counter
from typing import Any

STAGE_NAMES = frozenset(
    {
        "embedding_prepare",
        "embedding_compute",
        "embedding_evidence",
        "embedding_finalize",
        "memory_prepare",
        "memory_fetch",
        "memory_validate",
        "postgres_local_wait",
        "postgres_lock_wait",
        "postgres_transaction",
        "log_write",
    }
)


class StageTimings:
    def __init__(self) -> None:
        self._lock = Lock()
        self._closed = False
        self._durations: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    def add(self, name: str, duration_ms: float) -> None:
        with self._lock:
            if self._closed or name not in STAGE_NAMES or not math.isfinite(duration_ms):
                return
            self._durations[name] = self._durations.get(name, 0.0) + max(0.0, duration_ms)
            self._counts[name] = self._counts.get(name, 0) + 1

    def finish(self, *, partial: bool) -> dict[str, Any]:
        try:
            with self._lock:
                self._closed = True
                return {
                    "timings_ms": {k: round(v, 3) for k, v in self._durations.items()},
                    "timing_counts": dict(self._counts),
                    "timings_inclusive": True,
                    # A persisted record cannot include its own completed write
                    # without a second event/update. Only prior writes are timed.
                    "timings_exclude_terminal_write": True,
                    "timings_partial": partial,
                }
        except Exception:
            return {}


current_timings: ContextVar[StageTimings | None] = ContextVar("p3_stage_timings", default=None)


@contextmanager
def measure_stage(name: str) -> Iterator[None]:
    """Measure a static stage; telemetry failures never replace business errors."""
    collector = current_timings.get()
    started = None
    try:
        if collector is not None and name in STAGE_NAMES:
            started = perf_counter()
    except Exception:
        pass
    try:
        yield
    finally:
        if collector is not None and started is not None:
            with suppress(Exception):
                collector.add(name, (perf_counter() - started) * 1000)
