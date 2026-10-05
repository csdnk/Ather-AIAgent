"""Test-only RF and authorization doubles. Never wired into application bootstrap.

Transactions model atomicity in one process. They do NOT prove persistence,
database isolation, encryption, or recovery after a process restart.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from aether_agent_memory.recall.admission import RecallAuthorization, RecallInput
from aether_agent_memory.runtime.capability_store import RecordTransaction


class _MemoryTransaction:
    def __init__(self, values: dict[tuple[str, str, str], str]) -> None:
        self.values = values

    def get(self, namespace: str, tenant: str, key: str) -> str | None:
        return self.values.get((namespace, tenant, key))

    def put(self, namespace: str, tenant: str, key: str, value: str) -> None:
        self.values[namespace, tenant, key] = value

    def delete(self, namespace: str, tenant: str, key: str) -> None:
        self.values.pop((namespace, tenant, key), None)

    def scan(self, namespace: str) -> list[tuple[str, str, str]]:
        return [(t, k, v) for (n, t, k), v in self.values.items() if n == namespace]


class InMemoryRecallRecords:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], str] = {}
        self._lock = RLock()

    @contextmanager
    def transaction(self) -> Iterator[RecordTransaction]:
        with self._lock:
            pending = dict(self._values)
            yield _MemoryTransaction(pending)
            self._values = pending


class StaticRecallAuthority:
    """Return only configured fixture evidence, never authorize supplied IDs by default."""

    def __init__(self, evidence: RecallAuthorization) -> None:
        self.evidence = evidence
        self.calls = 0

    async def authorize(self, request: RecallInput) -> RecallAuthorization:
        self.calls += 1
        return RecallAuthorization.model_validate_json(self.evidence.model_dump_json())
