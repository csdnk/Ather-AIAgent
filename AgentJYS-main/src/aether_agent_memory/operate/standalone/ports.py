"""Business code depends on this interface; simulator controls are deliberately excluded."""

from typing import Protocol

from .models import Feedback, Intent, Memory, MemoryKey, Observation, Tier


class P2Port(Protocol):
    async def observe(self, key: MemoryKey) -> Observation | None: ...
    async def resources(self) -> dict[Tier, int]: ...
    async def submit(self, intent: Intent) -> Feedback: ...
    async def query(self, action_id: str) -> Feedback: ...
    async def verify_read(self, memory: Memory, tier: Tier) -> bool: ...


class SubmissionRejectedError(RuntimeError):
    """Provider guarantees no action was registered; a later new plan is safe."""
