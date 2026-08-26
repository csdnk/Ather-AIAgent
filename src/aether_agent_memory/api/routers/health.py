from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from aether_agent_memory.api.dependencies import runtime_of, settings_of
from aether_agent_memory.demo.service import STATE, observability_snapshot

router = APIRouter()


@router.get("/api/flows")
async def flows() -> dict[str, Any]:
    return {"items": STATE["flow_history"]}


@router.get("/api/schedules")
async def schedules() -> dict[str, Any]:
    return {"items": STATE["schedule_history"]}


@router.get("/health")
@router.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    runtime = runtime_of(request)
    settings = settings_of(request)
    health = await runtime.health()
    return {
        "p2_online": health.ready,
        "p2_endpoint": settings.p2_endpoint,
        "runtime_profile": health.runtime_profile,
        "runtime_health": health.to_dict(),
        "demo_enabled": settings.enable_demo,
        "app": settings.safe_status(),
        **observability_snapshot(),
    }
