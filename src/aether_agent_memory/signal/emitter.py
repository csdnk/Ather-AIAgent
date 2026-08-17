from aether_agent_memory.signal.models import MemorySignal


class MockSignalEmitter:
    def __init__(self) -> None:
        self._signals: list[MemorySignal] = []

    async def emit(self, signal: MemorySignal) -> None:
        self._signals.append(signal)

    @property
    def signals(self) -> list[MemorySignal]:
        return list(self._signals)

    def clear(self) -> None:
        self._signals.clear()
