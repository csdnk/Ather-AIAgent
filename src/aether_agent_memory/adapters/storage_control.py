"""HTTP binding for the representation control contract; no P2 route fiction."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.operate.models import (
    ActionFeedback,
    ActionState,
    PlacementObservation,
    RepresentationRef,
    TierAction,
)
from aether_agent_memory.operate.ports import StorageControlUnavailableError


class UnavailableStorageControl:
    async def submit(self, action: TierAction) -> ActionFeedback:
        raise StorageControlUnavailableError(
            "representation storage-control provider is not configured"
        )

    async def query(self, action: TierAction) -> ActionFeedback | None:
        raise StorageControlUnavailableError(
            "representation storage-control provider is not configured"
        )

    async def observe(
        self, representation: RepresentationRef, scope: Scope
    ) -> PlacementObservation:
        raise StorageControlUnavailableError(
            "representation placement observation is not configured"
        )


class HttpStorageControl:
    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        if urlparse(url).scheme not in {"http", "https"}:
            raise ValueError("storage control URL must use http or https")
        self.client = httpx.AsyncClient(base_url=url.rstrip("/") + "/", timeout=timeout_seconds)

    async def submit(self, action: TierAction) -> ActionFeedback:
        response = await self.client.post("actions", json=action.model_dump(mode="json"))
        if 400 <= response.status_code < 500:
            return ActionFeedback(
                action_id=action.action_id,
                state=ActionState.FAILED,
                error=f"control request rejected: HTTP {response.status_code}",
            )
        response.raise_for_status()
        return ActionFeedback.model_validate(response.json())

    async def query(self, action: TierAction) -> ActionFeedback | None:
        response = await self.client.post("actions/query", json=action.model_dump(mode="json"))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return ActionFeedback.model_validate(response.json())

    async def observe(
        self, representation: RepresentationRef, scope: Scope
    ) -> PlacementObservation:
        response = await self.client.post(
            "placements/query",
            json={
                "representation": representation.model_dump(mode="json"),
                "scope": scope.as_dict(),
            },
        )
        response.raise_for_status()
        return PlacementObservation.model_validate(response.json())

    async def close(self) -> None:
        await self.client.aclose()
