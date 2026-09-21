"""Provider-specific placement compatibility models."""

from __future__ import annotations

from aether_agent_memory.core.enums import StorageTier
from aether_agent_memory.core.memory import MemoryPlacement


class P2Ref(MemoryPlacement):
    """Deprecated P2 placement alias kept outside the core Memory domain."""

    provider: str = "p2"
    segment_id: str
    object_key: str
    tier: StorageTier = StorageTier.L0_DRAM
