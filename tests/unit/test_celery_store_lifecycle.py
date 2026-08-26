"""Regression tests for sync Celery tasks using async persistence adapters."""

from __future__ import annotations

import asyncio

import pytest

celery_app = pytest.importorskip("aether_agent_memory.b2.celery_app")

from aether_agent_memory.core.enums import MemoryType  # noqa: E402
from aether_agent_memory.core.memory import Memory  # noqa: E402


def test_persist_memory_closes_store_on_same_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LoopBoundStore:
        loop: asyncio.AbstractEventLoop | None = None
        closed = False

        async def upsert(self, memory: Memory) -> Memory:
            self.loop = asyncio.get_running_loop()
            return memory

        async def close(self) -> None:
            assert asyncio.get_running_loop() is self.loop
            self.closed = True

    store = LoopBoundStore()
    monkeypatch.setattr(celery_app, "_memory_store", lambda: store)
    memory = Memory(
        type=MemoryType.EPISODIC,
        session_id="session",
        agent_id="agent",
        content="server regression",
    )

    celery_app._persist_memory(memory)

    assert store.closed is True
