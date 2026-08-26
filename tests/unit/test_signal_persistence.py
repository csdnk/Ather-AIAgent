"""Redis signal emitter persistence tests."""

from __future__ import annotations

from typing import Any

import pytest

from aether_agent_memory.core.enums import MemoryType, SignalType
from aether_agent_memory.signal.emitter import RedisSignalEmitter
from aether_agent_memory.signal.models import MemorySignal


class _FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.calls.append(("zadd", key, mapping))

    def expire(self, key: str, ttl: int) -> None:
        self.calls.append(("expire", key, ttl))

    def close(self) -> None:
        self.calls.append(("close",))


@pytest.mark.asyncio
async def test_redis_signal_emitter_persists_signal() -> None:
    fake = _FakeRedis()
    emitter = RedisSignalEmitter("redis://localhost:6379/0", client=fake)
    signal = MemorySignal(
        memory_id="m1",
        memory_type=MemoryType.SEMANTIC,
        session_id="s",
        agent_id="a",
        signal_type=SignalType.CREATION,
    )

    await emitter.emit(signal)
    await emitter.close()

    assert fake.calls[0][0] == "zadd"
    assert fake.calls[0][1] == "p3:signal:m1"
    member = next(iter(fake.calls[0][2]))
    assert '"memory_id":"m1"' in member
    assert fake.calls[1][0] == "expire"
    assert fake.calls[-1][0] == "close"


@pytest.mark.asyncio
async def test_redis_signal_emitter_rejects_bad_ttl() -> None:
    with pytest.raises(ValueError, match="signal TTL"):
        RedisSignalEmitter("redis://localhost:6379/0", ttl_seconds=0)
