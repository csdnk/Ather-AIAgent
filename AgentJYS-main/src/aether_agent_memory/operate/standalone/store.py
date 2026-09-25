"""Demo-only bounded residency and notification indexes; statistics outlive Buffer0."""

import heapq
from collections import OrderedDict, deque
from dataclasses import dataclass

from .models import Intent, MemoryKey, Stats
from .policy import Settings


@dataclass
class Pending:
    intent: Intent
    sequence: int


class MemoryStore:
    def __init__(self, settings: Settings) -> None:
        self.stats: dict[MemoryKey, Stats] = {}
        self.buffer: OrderedDict[MemoryKey, None] = OrderedDict()
        self.ready: OrderedDict[MemoryKey, set[str]] = OrderedDict()
        self.pending: dict[MemoryKey, Pending] = {}
        self.dedup: OrderedDict[tuple[MemoryKey, int, str], float] = OrderedDict()
        self.history: deque[dict[str, object]] = deque(maxlen=settings.history_limit)
        self.timers: dict[MemoryKey, tuple[float, int]] = {}
        self._heap: list[tuple[float, int, MemoryKey]] = []
        self._serial = 0

    def wake(self, key: MemoryKey, reason: str) -> None:
        self.ready.setdefault(key, set()).add(reason)

    def schedule(self, key: MemoryKey, due: float) -> None:
        self._serial += 1
        value = (due, self._serial)
        self.timers[key] = value
        heapq.heappush(self._heap, (*value, key))
        self._compact_timers()

    def _compact_timers(self) -> None:
        # Replacing/reclaiming timers must not leave an unbounded stale-entry heap.
        if len(self._heap) > 2 * len(self.timers) + 32:
            self._heap = [(time, serial, key) for key, (time, serial) in self.timers.items()]
            heapq.heapify(self._heap)

    def wake_due(self, now: float, limit: int) -> int:
        count = 0
        examined = 0
        while self._heap and self._heap[0][0] <= now and examined < limit:
            due, serial, key = heapq.heappop(self._heap)
            examined += 1
            if self.timers.get(key) != (due, serial):
                continue
            del self.timers[key]
            self.wake(key, "timer")
            count += 1
        return count

    def forget(self, key: MemoryKey, expected: Stats, sequence: int) -> bool:
        """Atomic in this single event loop; TTL dedup and P2 objects survive."""
        if (
            self.stats.get(key) is not expected
            or expected.sequence != sequence
            or key in self.pending
            or key in self.ready
        ):
            return False
        del self.stats[key]
        self.buffer.pop(key, None)
        self.timers.pop(key, None)
        self._compact_timers()
        return True

    def expire_dedup(self, now: float, limit: int) -> None:
        for _ in range(limit):
            if not self.dedup:
                break
            key, expiry = next(iter(self.dedup.items()))
            if expiry > now:
                break
            del self.dedup[key]
