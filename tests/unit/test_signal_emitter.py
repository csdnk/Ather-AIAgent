import pytest

from aether_agent_memory.core.enums import MemoryType, SignalType
from aether_agent_memory.signal.emitter import MockSignalEmitter
from aether_agent_memory.signal.models import MemorySignal


def _make_signal(signal_type: SignalType = SignalType.ACCESS) -> MemorySignal:
    return MemorySignal(
        memory_id="m1",
        memory_type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        signal_type=signal_type,
        heat=0.5,
    )


@pytest.mark.unit
async def test_emit_collects_signals() -> None:
    emitter = MockSignalEmitter()
    await emitter.emit(_make_signal())
    await emitter.emit(_make_signal(SignalType.EVICTION))
    assert len(emitter.signals) == 2
    assert emitter.signals[0].signal_type == SignalType.ACCESS
    assert emitter.signals[1].signal_type == SignalType.EVICTION


@pytest.mark.unit
async def test_clear_resets_signals() -> None:
    emitter = MockSignalEmitter()
    await emitter.emit(_make_signal())
    emitter.clear()
    assert emitter.signals == []


@pytest.mark.unit
async def test_signals_is_readonly_view() -> None:
    emitter = MockSignalEmitter()
    await emitter.emit(_make_signal())
    sigs = emitter.signals
    assert len(sigs) == 1
