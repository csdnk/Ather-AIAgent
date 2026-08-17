from typing import Protocol, runtime_checkable

from aether_agent_memory.context.models import ContextPack, ContextRequest


@runtime_checkable
class ContextPackBuilder(Protocol):
    async def build(self, request: ContextRequest) -> ContextPack: ...
