"""Contract and error-model tests for the FastAPI application host."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aether_agent_memory.app import create_app
from aether_agent_memory.config.app_settings import AppSettings

NORTHBOUND_PATHS = {
    "/health",
    "/api/v1/memory/events",
    "/api/v1/context",
    "/api/v1/b2/long-text",
    "/api/v1/b2/tasks/{task_id}",
    "/api/v1/b2/search",
}


@pytest.fixture()
def client():
    app = create_app(AppSettings(profile="demo"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.unit
def test_openapi_exposes_northbound_paths(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"].keys())
    assert paths >= NORTHBOUND_PATHS


@pytest.mark.unit
def test_value_error_maps_to_422_not_500(client: TestClient) -> None:
    response = client.post("/api/v1/b1/embeddings", json={"text": ""})
    assert response.status_code == 422
    assert response.json()["error"] == "text is required"


@pytest.mark.unit
def test_demo_disabled_returns_404(client: TestClient) -> None:
    assert client.post("/api/demo/session", json={}).status_code == 404
    assert client.post("/api/run-smoke").status_code == 404


@pytest.mark.unit
def test_flows_and_schedules_read_contract(client: TestClient) -> None:
    flows = client.get("/api/flows")
    assert flows.status_code == 200
    assert flows.json() == {"items": []}
    schedules = client.get("/api/schedules")
    assert schedules.status_code == 200
    assert schedules.json() == {"items": []}


@pytest.mark.unit
def test_unknown_route_is_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/does-not-exist").status_code == 404
