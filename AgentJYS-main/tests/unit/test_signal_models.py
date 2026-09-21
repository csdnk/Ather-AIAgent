from datetime import UTC, datetime

import pytest

from aether_agent_memory.core.enums import MemoryType, SignalType
from aether_agent_memory.signal.models import MemorySignal


@pytest.mark.unit
def test_signal_defaults() -> None:
    sig = MemorySignal(
        memory_id="m1",
        memory_type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        signal_type=SignalType.ACCESS,
    )
    assert sig.heat == 0.0
    assert sig.metadata == {}
    assert sig.timestamp <= datetime.now(UTC)


@pytest.mark.unit
def test_signal_with_heat_and_metadata() -> None:
    sig = MemorySignal(
        memory_id="m1",
        memory_type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        signal_type=SignalType.PROMOTION_HINT,
        heat=0.87,
        metadata={"reason": "high_access"},
    )
    assert sig.heat == 0.87
    assert sig.metadata["reason"] == "high_access"
