from __future__ import annotations

from typing import Any, cast

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from aether_agent_memory.config.app_settings import AppSettings
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.service import MemoryRuntime


def runtime_of(request: Request) -> MemoryRuntime:
    return cast(MemoryRuntime, request.app.state.runtime)


def settings_of(request: Request) -> AppSettings:
    return cast(AppSettings, request.app.state.settings)


def enforce_scope(body: Any) -> None:
    """Northbound scope gate: tenant/user/agent must be present."""
    tenant = getattr(body, "tenant_id", None)
    user = getattr(body, "user_id", None)
    agent = getattr(body, "agent_id", None)
    if not tenant or not user or not agent:
        raise ScopeError("tenant_id, user_id and agent_id are required")


def json_model(model: Any) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    if not isinstance(payload, dict):
        raise TypeError("model_dump did not return a mapping")
    return dict(payload)


def json_response(
    request: Request,
    payload: Any,
    *,
    status_code: int = 200,
) -> Response:
    settings = settings_of(request)
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers={"Access-Control-Allow-Origin": ",".join(settings.cors_origins)},
    )
