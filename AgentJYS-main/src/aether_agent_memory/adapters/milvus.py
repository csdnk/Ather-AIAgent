from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from aether_agent_memory.runtime.status import ComponentHealth, ComponentStatus, RuntimeComponent


class MilvusHealthAdapter:
    def __init__(self, uri: str, *, probe_timeout_seconds: float = 2.0) -> None:
        self._uri = uri
        self._timeout = probe_timeout_seconds

    async def health(self) -> ComponentHealth:
        if not self._uri:
            return ComponentHealth(
                component=RuntimeComponent.MILVUS,
                status=ComponentStatus.DEGRADED,
                detail="Milvus URI is not configured; long-term vector projection is optional",
                critical=False,
            )
        try:
            from pymilvus import connections, utility

            parsed = urlparse(self._uri)
            host = parsed.hostname or "localhost"
            port = parsed.port or 19530
            alias = "p3-health"

            def _probe() -> None:
                connections.connect(
                    alias=alias, host=host, port=port, timeout=self._timeout
                )
                try:
                    utility.has_collection("__p3_health_probe__", using=alias)
                finally:
                    connections.disconnect(alias)

            await asyncio.to_thread(_probe)
            return ComponentHealth(
                component=RuntimeComponent.MILVUS,
                status=ComponentStatus.HEALTHY,
                detail=f"Milvus reachable at {host}:{port}",
                critical=False,
            )
        except Exception as exc:
            return ComponentHealth(
                component=RuntimeComponent.MILVUS,
                status=ComponentStatus.UNAVAILABLE,
                detail=f"{type(exc).__name__}: {exc}",
                critical=False,
            )
