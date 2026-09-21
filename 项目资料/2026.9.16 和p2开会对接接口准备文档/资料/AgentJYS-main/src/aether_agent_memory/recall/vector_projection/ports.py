"""A's mechanism contract for B; it never mutates B's domain ProjectionState."""

from typing import Protocol

from aether_agent_memory.recall.vector_projection.models import (
    ProviderResult,
    VectorProjectionRequest,
)


class VectorProjectionPort(Protocol):
    async def submit(self, request: VectorProjectionRequest) -> ProviderResult: ...
    async def query(
        self, tenant_id: str, caller_ref: str, authorization_ref: str, operation_id: str
    ) -> ProviderResult: ...
