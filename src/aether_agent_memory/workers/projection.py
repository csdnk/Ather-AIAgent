"""Standalone worker for durable Memory projection jobs."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from aether_agent_memory.bootstrap import build_runtime
from aether_agent_memory.config.app_settings import AppSettings
from aether_agent_memory.runtime.service import MemoryRuntime


async def run_once(*, limit: int | None = None) -> dict[str, Any]:
    runtime = _build_runtime()
    try:
        report = await runtime.drain_projection_work(
            limit=(
                _positive_int("AETHER_P3_PROJECTION_WORKER_BATCH", 100)
                if limit is None
                else limit
            )
        )
        return report.model_dump(mode="json")
    finally:
        await runtime.close()


async def run_forever() -> None:
    interval = _positive_float("AETHER_P3_PROJECTION_WORKER_POLL_SECONDS", 1.0)
    runtime = _build_runtime()
    try:
        while True:
            report = await runtime.drain_projection_work(
                limit=_positive_int("AETHER_P3_PROJECTION_WORKER_BATCH", 100)
            )
            if report.requested:
                print(
                    f"projection worker report: {report.model_dump(mode='json')}",
                    flush=True,
                )
            await asyncio.sleep(interval)
    finally:
        await runtime.close()


def main() -> None:
    if os.getenv("AETHER_P3_WORKER_ONCE", "false").lower() in {"1", "true", "yes"}:
        print(asyncio.run(run_once()), flush=True)
        return
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:  # pragma: no cover - process boundary
        return


def _build_runtime() -> MemoryRuntime:
    settings = AppSettings()
    settings.validate_for_profile()
    return build_runtime(settings)


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
