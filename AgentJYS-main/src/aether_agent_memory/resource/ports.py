from __future__ import annotations

from typing import Protocol

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.resource.models import ResourceRecord
from aether_agent_memory.resource.parsing import (
    ParsedResourceContent,
    ResourceContentParserPort,
)


class ResourceStorePort(Protocol):
    async def get(self, scope: Scope, resource_id: str) -> ResourceRecord | None: ...

    async def reserve_revision(
        self, scope: Scope, resource_id: str, *, minimum: int = 0
    ) -> int: ...

    async def upsert(self, resource: ResourceRecord) -> None: ...

    async def delete(self, scope: Scope, resource_id: str) -> bool: ...

    async def list_scoped(
        self,
        *,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str | None,
        category: str | None = None,
    ) -> list[ResourceRecord]: ...


__all__ = [
    "ParsedResourceContent",
    "ResourceContentParserPort",
    "ResourceStorePort",
]
