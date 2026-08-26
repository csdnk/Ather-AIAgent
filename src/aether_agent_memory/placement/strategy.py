"""Vector namespace strategies live at the adapter/placement boundary."""

from __future__ import annotations

from hashlib import sha256
from typing import Protocol


class VectorNamespaceStrategy(Protocol):
    def collection_for_scope(
        self,
        base: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        dimension: int,
    ) -> str: ...


class PerUserAgentVectorNamespaceStrategy:
    """Current P2 E1 strategy; not a Memory Domain rule."""

    def collection_for_scope(
        self,
        base: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        dimension: int,
    ) -> str:
        if dimension <= 0:
            raise ValueError("P2 collection dimension must be positive")
        scope = "\0".join((tenant_id, user_id, agent_id)).encode("utf-8")
        return f"{base}-b2-d{dimension}-{sha256(scope).hexdigest()[:20]}"
