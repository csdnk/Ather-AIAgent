from aether_agent_memory.core.enums import (
    MemoryState,
    MemoryType,
    SignalType,
    SourceType,
    StorageTier,
)


def test_memory_type_values() -> None:
    assert MemoryType.WORKING == "working"
    assert MemoryType.EPISODIC == "episodic"
    assert MemoryType.SEMANTIC == "semantic"


def test_memory_state_values() -> None:
    assert MemoryState.ACTIVE == "active"
    assert MemoryState.ARCHIVED == "archived"
    assert MemoryState.EXPIRED == "expired"
    assert MemoryState.SUPERSEDED == "superseded"


def test_source_type_values() -> None:
    assert SourceType.USER == "user"
    assert SourceType.AGENT == "agent"
    assert SourceType.SYSTEM == "system"
    assert SourceType.TOOL == "tool"
    assert SourceType.DISTILLED == "distilled"


def test_signal_type_values() -> None:
    assert SignalType.ACCESS == "access"
    assert SignalType.EVICTION == "eviction"
    assert SignalType.PROMOTION_HINT == "promotion_hint"
    assert SignalType.DEMOTION_HINT == "demotion_hint"
    assert SignalType.ARCHIVAL == "archival"
    assert SignalType.CREATION == "creation"


def test_storage_tier_values() -> None:
    assert StorageTier.L0_DRAM == "L0"
    assert StorageTier.L1_NVME == "L1"
    assert StorageTier.L2_HDD == "L2"
    assert StorageTier.L3_OBJECT == "L3"
    assert StorageTier.L4_ARCHIVE == "L4"


def test_enums_are_str() -> None:
    assert isinstance(MemoryType.WORKING, str)
    assert isinstance(MemoryState.ACTIVE, str)
