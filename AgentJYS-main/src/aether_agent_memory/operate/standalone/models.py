"""Demo-local contracts: proposed semantics, not a confirmed P2 protocol."""

from dataclasses import dataclass
from typing import Literal

Tier = Literal["cold", "warm", "hot"]
Operation = Literal["ensure_hot_replica", "remove_hot_replica", "move_base"]


@dataclass(frozen=True)
class MemoryKey:
    tenant: str
    memory_id: str
    application: str = "demo"
    user: str = "demo"
    agent: str = "demo"


@dataclass(frozen=True)
class Memory:
    key: MemoryKey
    version: int
    content_hash: str
    size: int
    importance: float = 0.5

    def __post_init__(self) -> None:
        if self.version < 1 or self.size < 1 or not 0 <= self.importance <= 1:
            raise ValueError("invalid memory metadata")


@dataclass(frozen=True)
class Observation:
    memory: Memory
    epoch: int
    base: Tier
    hot: bool
    known: bool = True
    readable: bool = True


@dataclass(frozen=True)
class Intent:
    action_id: str
    memory: Memory
    epoch: int
    operation: Operation
    target: Tier


@dataclass(frozen=True)
class Feedback:
    action_id: str
    state: Literal["running", "succeeded", "failed", "unknown", "not_found"]
    reason: str = ""


@dataclass
class Stats:
    memory: Memory
    updated_at: float
    last_access: float | None = None
    access_sum: float = 0.0
    access_count: int = 0
    sequence: int = 0
    desired: Tier = "cold"
    heat: float = 0.0
    evaluated_at: float | None = None
    last_reason: str = "registered"
    cold_since: float | None = None
