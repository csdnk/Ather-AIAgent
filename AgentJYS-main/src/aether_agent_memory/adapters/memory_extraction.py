"""HTTP adapter for an optional semantic Session-to-Memory provider."""

from __future__ import annotations

import httpx

from aether_agent_memory.memory.formation import (
    MemoryExtractionCandidate,
    MemoryExtractionPort,
)
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.session.models import SessionArchive


class HttpMemoryExtractionAdapter(MemoryExtractionPort):
    """Call a company-owned extraction gateway with a strict JSON contract.

    The gateway is deliberately provider-neutral: P3 sends an immutable archive
    and receives only typed formation candidates. No LLM SDK or vendor response
    model crosses into the Session or Memory domain.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        model: str = "",
        api_key: str = "",
        timeout_seconds: float = 30.0,
        max_candidates: int = 20,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not endpoint.strip():
            raise ValueError("memory extraction endpoint must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("memory extraction timeout must be positive")
        if max_candidates < 1:
            raise ValueError("memory extraction max_candidates must be positive")
        self._endpoint = endpoint.strip()
        self._model = model.strip()
        self._api_key = api_key.strip()
        self._max_candidates = max_candidates
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def extract(
        self,
        archive: SessionArchive,
        context: RequestContext,
    ) -> list[MemoryExtractionCandidate]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = await self._client.post(
            self._endpoint,
            headers=headers,
            json={
                "model": self._model or None,
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "scope": context.scope.as_dict(),
                "archive": archive.model_dump(mode="json"),
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("memory extraction provider response must be an object")
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError("memory extraction response requires candidates list")
        if len(raw_candidates) > self._max_candidates:
            raise ValueError(
                "memory extraction provider returned too many candidates: "
                f"{len(raw_candidates)} > {self._max_candidates}"
            )
        candidates: list[MemoryExtractionCandidate] = []
        for index, raw_candidate in enumerate(raw_candidates):
            if not isinstance(raw_candidate, dict):
                raise ValueError(f"memory extraction candidate {index} is not an object")
            candidates.append(MemoryExtractionCandidate.model_validate(raw_candidate))
        return candidates

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = ["HttpMemoryExtractionAdapter"]
