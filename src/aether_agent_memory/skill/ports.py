from __future__ import annotations

from typing import Protocol

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.skill.models import SkillRecord


class SkillStorePort(Protocol):
    async def reserve_revision(
        self, scope: Scope, skill_id: str, *, minimum: int = 0
    ) -> int: ...

    async def upsert(self, skill: SkillRecord) -> None: ...

    async def get(self, scope: Scope, skill_id: str) -> SkillRecord | None: ...

    async def delete(self, scope: Scope, skill_id: str) -> bool: ...

    async def list_scoped(
        self,
        *,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str | None,
    ) -> list[SkillRecord]: ...
