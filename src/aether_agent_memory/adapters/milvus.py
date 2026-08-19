from __future__ import annotations

from aether_agent_memory.runtime.status import ComponentHealth, ComponentStatus, RuntimeComponent


class MilvusHealthAdapter:
    def __init__(self, uri: str) -> None:
        self._uri = uri

    async def health(self) -> ComponentHealth:
        if not self._uri:
            return ComponentHealth(
                component=RuntimeComponent.MILVUS,
                status=ComponentStatus.DEGRADED,
                detail="Milvus URI is not configured; long-term vector projection is optional",
                critical=False,
            )
        return ComponentHealth(
            component=RuntimeComponent.MILVUS,
            status=ComponentStatus.UNKNOWN,
            detail="Milvus projection is adapter-owned; active probe is not wired in this refactor",
            critical=False,
        )
