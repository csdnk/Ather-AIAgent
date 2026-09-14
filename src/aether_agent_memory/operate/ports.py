from typing import Protocol

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.operate.models import (
    ActionFeedback,
    ActionState,
    PlacementObservation,
    RepresentationRef,
    TierAction,
)


class StorageControlPort(Protocol):
    async def submit(self, action: TierAction) -> ActionFeedback: ...
    async def query(self, action: TierAction) -> ActionFeedback | None: ...
    async def observe(
        self, representation: RepresentationRef, scope: Scope
    ) -> PlacementObservation: ...


class ActionJournalPort(Protocol):
    async def reserve(self, action: TierAction) -> TierAction: ...
    async def transition(self, action: TierAction, expected: set[ActionState]) -> bool: ...
    async def get(self, action_id: str) -> TierAction | None: ...
    async def pending(self, scope: Scope, *, limit: int = 100) -> list[TierAction]: ...
    async def close(self) -> None: ...


class StorageControlUnavailableError(RuntimeError):
    """Definite non-submission: no compatible actuator has been configured."""
