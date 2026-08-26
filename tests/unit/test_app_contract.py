"""Contract and error-model tests for the FastAPI application host."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aether_agent_memory.app import create_app
from aether_agent_memory.config.app_settings import AppSettings

ROOT = Path(__file__).parents[2]
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


@pytest.mark.unit
def test_create_app_uses_passed_settings_not_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AETHER_P2_GRPC", "env-p2:9999")
    monkeypatch.setenv("AETHER_B1_SIDECAR_URL", "http://env-b1:18081")
    settings = AppSettings(
        profile="demo",
        data_dir=str(tmp_path),
        p2_endpoint="settings-p2:1234",
        p2_engine="settings-engine",
        p2_bucket="settings-bucket",
        p2_collection="settings-collection",
        redis_url="redis://settings-redis:6379/0",
        b1_sidecar_url="http://settings-b1:18081",
        b1_model_name="settings-model",
    )

    app = create_app(settings)
    with TestClient(app) as test_client:
        runtime = test_client.app.state.runtime
        config = runtime.legacy_runtime.config

    assert config.p2_endpoint == "settings-p2:1234"
    assert config.p2_engine == "settings-engine"
    assert config.p2_bucket == "settings-bucket"
    assert config.p2_collection == "settings-collection"
    assert config.redis_url == "redis://settings-redis:6379/0"
    assert config.b1_sidecar_url == "http://settings-b1:18081"
    assert config.b1_model_name == "settings-model"


@pytest.mark.unit
def test_app_and_api_routers_do_not_use_legacy_runtime_client() -> None:
    files = [ROOT / "src" / "aether_agent_memory" / "app.py"]
    files.extend((ROOT / "src" / "aether_agent_memory" / "api").rglob("*.py"))
    combined = "\n".join(path.read_text("utf-8") for path in files)

    assert "runtime.client" not in combined
    assert "P3RuntimeConfig.from_environment" not in combined


@pytest.mark.unit
def test_p3_service_is_compatibility_wrapper() -> None:
    source = (ROOT / "scripts" / "p3_service.py").read_text("utf-8")

    assert "from aether_agent_memory.app import main" in source
    assert "HTTPServer" not in source
    assert "MemoryRuntime.from_config" not in source
    assert "P3RuntimeConfig.from_environment" not in source


@pytest.mark.unit
def test_container_entrypoints_use_application_host() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    compose = (ROOT / "compose.yaml").read_text("utf-8")

    assert 'CMD ["python", "-m", "aether_agent_memory.app"]' in dockerfile
    assert 'command: ["python", "-m", "aether_agent_memory.app"]' in compose
