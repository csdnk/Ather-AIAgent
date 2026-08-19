from __future__ import annotations

from aether_agent_memory.memory.retrieval.models import AccessTrace


class InMemoryAccessTraceAdapter:
    def __init__(self) -> None:
        self._items: list[AccessTrace] = []

    async def record(self, trace: AccessTrace) -> None:
        self._items.append(trace)

    @property
    def items(self) -> list[AccessTrace]:
        return list(self._items)
