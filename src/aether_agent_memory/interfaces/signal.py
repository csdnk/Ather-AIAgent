from typing import Protocol, runtime_checkable

from aether_agent_memory.signal.models import MemorySignal


@runtime_checkable
class SignalEmitter(Protocol):
    async def emit(self, signal: MemorySignal) -> None: ...
