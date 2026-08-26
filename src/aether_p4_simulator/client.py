from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4

import httpx


class P3ClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class P3Client(Protocol):
    base_url: str

    async def health(self) -> dict[str, Any]: ...

    async def build_context(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        query: str,
        max_tokens: int,
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]: ...

    async def write_memory(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        content: str,
        event_type: str,
        source: str,
        metadata: dict[str, Any],
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]: ...

    async def submit_long_text(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        text: str,
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]: ...

    async def get_task(self, task_id: str) -> dict[str, Any]: ...


class P3MemoryClient:
    """HTTP-only adapter for the frozen P3 northbound v1 contract."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise P3ClientError(f"P3 unavailable: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise P3ClientError(
                "P3 returned a non-JSON response",
                status_code=502,
                payload=response.text,
            ) from exc
        if not isinstance(body, dict):
            raise P3ClientError("P3 returned an invalid JSON object", payload=body)
        if response.is_error:
            message = str(body.get("error") or body.get("detail") or response.reason_phrase)
            raise P3ClientError(message, status_code=response.status_code, payload=body)
        return dict(body)

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def build_context(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        query: str,
        max_tokens: int,
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/context",
            payload={
                "tenant_id": tenant_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "query": query,
                "max_tokens": max_tokens,
                "max_candidates": 20,
                "request_id": request_id,
                "trace_id": trace_id,
            },
        )

    async def write_memory(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        content: str,
        event_type: str,
        source: str,
        metadata: dict[str, Any],
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/memory/events",
            payload={
                "tenant_id": tenant_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "content": content,
                "event_type": event_type,
                "source": source,
                "importance": 0.9,
                "metadata": metadata,
                "request_id": request_id,
                "trace_id": trace_id,
                "idempotency_key": f"p4-memory-{request_id}",
            },
        )

    async def submit_long_text(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        text: str,
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/b2/long-text",
            payload={
                "tenant_id": tenant_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "source_id": source_id,
                "text": text,
                "request_id": request_id,
                "trace_id": trace_id,
                "idempotency_key": f"p4-document-{request_id}",
            },
        )

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/b2/tasks/{task_id}")


def new_request_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"
