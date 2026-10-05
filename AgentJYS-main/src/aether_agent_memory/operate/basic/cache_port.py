"""Backend-neutral cache inspection and execution; copies are optional replicas."""

from dataclasses import dataclass
from typing import Protocol

from aether_agent_memory.operate.contracts.models import (
    ActionIntent,
    ExecutionFeedback,
    PlacementObservation,
    ReadProof,
    ResourceSnapshot,
    Tier,
)
from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot
from aether_agent_memory.runtime.contracts.models import Scope, TrustedContext


@dataclass(frozen=True)
class CacheCopy:
    key: str
    memory: MemoryRef
    tier: Tier
    content_hash: str


class CacheExecutor(Protocol):
    provider_id: str
    instance_id: str
    mode: str
    capacity: int

    def ensure(self, memory: MemorySnapshot, ctx: TrustedContext | None = None) -> None: ...
    def repair(
        self, memory: MemorySnapshot, operation_id: str, ctx: TrustedContext | None = None
    ) -> None: ...
    def purge(
        self, memory: MemoryRef, *, permanent: bool, ctx: TrustedContext | None = None
    ) -> None: ...
    def inspect(self, memory: MemoryRef, digest: str) -> bool: ...
    def copies(
        self, cursor: str = "", limit: int = 16, *, digest: str | None = None
    ) -> list[CacheCopy]: ...
    def repair_record(self, operation_id: str) -> tuple[str, str] | None: ...
    def cleanup_complete(self, memory: MemoryRef, *, permanent: bool) -> bool: ...
    def read_cached(self, scope: Scope, digest: str) -> str | None: ...
    def probe(self) -> dict[str, object]: ...
    async def resources(self, ctx: TrustedContext) -> ResourceSnapshot: ...
    async def observe(
        self, ctx: TrustedContext, memory: MemoryRef, representation_id: str
    ) -> PlacementObservation: ...
    async def submit(self, ctx: TrustedContext, intent: ActionIntent) -> ExecutionFeedback: ...
    async def query(self, ctx: TrustedContext, action_id: str) -> ExecutionFeedback: ...
    async def verify_read(self, ctx: TrustedContext, intent: ActionIntent) -> ReadProof: ...


class CacheCapacityError(RuntimeError):
    """An atomic cache reservation exceeded the configured capacity."""
