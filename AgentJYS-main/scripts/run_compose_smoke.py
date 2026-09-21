"""Run one real Docker Compose B1 -> P2 -> B2 -> B3 smoke flow and exit."""

from __future__ import annotations

import asyncio
import os
from time import monotonic
from typing import Any

import httpx

BASE_URL = os.getenv("AETHER_P3_BASE_URL", "http://p3:8080").rstrip("/")
STARTUP_TIMEOUT_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 120.0


async def _wait_until_ready(client: httpx.AsyncClient) -> None:
    deadline = monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error = "service did not respond"
    while monotonic() < deadline:
        try:
            response = await client.get("/health")
            if response.is_success:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(1)
    raise TimeoutError(
        f"P3 service was not ready within {STARTUP_TIMEOUT_SECONDS:g}s: {last_error}"
    )


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} is missing or invalid")
    return value


async def main() -> None:
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout) as client:
        await _wait_until_ready(client)
        response = await client.post("/api/run-smoke", json={})
        response.raise_for_status()
        payload = response.json()

    report = _require_mapping(payload.get("last_report"), "last_report")
    async_result = _require_mapping(report.get("b2_async"), "b2_async")
    action = _require_mapping(report.get("action"), "B3 action")
    if str(async_result.get("state", "")).lower() != "succeeded":
        raise RuntimeError(f"B2 Celery task did not succeed: {async_result.get('state')}")
    if int(async_result.get("p2_vector_count", 0)) < 1:
        raise RuntimeError("P2 E1 did not receive an async vector")
    if int(async_result.get("search_match_count", 0)) < 1:
        raise RuntimeError("P2 E1 search did not return the async vector")

    print(f"P2 object={report.get('object_key')}")
    print(f"vectors={async_result['p2_vector_count']}")
    print(f"B2 memories={len(report.get('context', {}).get('memories', []))}")
    print(f"B3 action={action.get('action_type')}")
    print("COMPOSE_SMOKE_PASSED")


if __name__ == "__main__":
    asyncio.run(main())
