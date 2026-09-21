from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from aether_agent_memory.api.dependencies import json_response, runtime_of, settings_of
from aether_agent_memory.demo.service import record_demo_run, run_full_test, update_last_success

try:
    from dashboard_page import DASHBOARD_HTML  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - dashboard_page lives in scripts/
    DASHBOARD_HTML = "<html><body>P3 dashboard unavailable</body></html>"

router = APIRouter()


@router.get("/", include_in_schema=False)
async def dashboard() -> Response:
    return Response(
        content=DASHBOARD_HTML.encode("utf-8"),
        media_type="text/html; charset=utf-8",
    )


@router.post("/api/demo/session", response_model=None)
async def demo_session(request: Request, body: dict[str, Any]) -> Response | dict[str, Any]:
    settings = settings_of(request)
    if not settings.enable_demo:
        return json_response(request, {"error": "demo mode is disabled"}, status_code=404)
    runtime = runtime_of(request)
    title = str(body.get("title", "")).strip()
    content = str(body.get("content", "")).strip()
    user_message = str(body.get("user_message", "")).strip()
    if not title or not content or not user_message:
        raise ValueError("title, content, and user_message are required")
    report = await run_full_test(
        runtime,
        title=title,
        content=content,
        user_message=user_message,
        session_id=str(body.get("session_id", "dashboard-session")),
    )
    record_demo_run("knowledge-session", report)
    update_last_success(report)
    return report


@router.post("/api/run-smoke", response_model=None)
async def run_smoke(request: Request) -> Response | dict[str, Any]:
    settings = settings_of(request)
    if not settings.enable_demo:
        return json_response(request, {"error": "demo mode is disabled"}, status_code=404)
    runtime = runtime_of(request)
    report = await run_full_test(
        runtime,
        title="Persistent service smoke test",
        content="Aether P3 writes a traceable semantic vector into the P2 engine.",
        user_message="Which document is available for semantic recall?",
        session_id="p3-service-smoke",
        agent_id="p3-service-agent",
        tenant_id="integration",
    )
    record_demo_run("integration-smoke", report)
    update_last_success(report)
    return report
