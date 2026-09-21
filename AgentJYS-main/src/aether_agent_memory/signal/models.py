from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from aether_agent_memory.core.enums import MemoryState, MemoryType, SignalType


class MemorySignal(BaseModel):
    memory_id: str
    memory_type: MemoryType
    session_id: str
    agent_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    signal_type: SignalType
    heat: float = 0.0
    importance: float = 0.0
    use_count: int = 0
    last_used_time: datetime | None = None
    ttl_seconds: int | None = None
    state: MemoryState = MemoryState.ACTIVE
    user_defined_flag: bool = False
    context_used_flag: bool = False
    correction_flag: bool = False
    source_id: str | None = None
    trace_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
