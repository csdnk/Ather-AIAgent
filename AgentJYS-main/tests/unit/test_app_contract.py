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
def test_projection_reconcile_route_is_wired_to_runtime() -> None:
    app = create_app(AppSettings(profile="demo", memory_store="sqlite"))
    with TestClient(app) as local_client:
        response = local_client.post(
            "/api/v1/projections/reconcile",
            json={
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "agent_id": "agent-1",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["items"], list)
    assert payload["request_id"]
    assert payload["trace_id"]
    assert all(
        item["scope"]["tenant_id"] == "tenant-1" for item in payload["items"]
    )


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
def test_context_catalog_routes_use_runtime_and_preserve_scope(client: TestClient) -> None:
    local_app = create_app(AppSettings(profile="demo", memory_store="sqlite"))
    scope = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "agent_id": "agent-1",
        "session_id": "session-1",
    }
    root = "aether://tenants/tenant-1/users/user-1/agents/agent-1/sessions/session-1"

    with TestClient(local_app) as local_client:
        children = local_client.post(
            "/api/v1/context/catalog/children",
            json={**scope, "parent_uri": root},
        )
        assert children.status_code == 200
        assert children.json()["items"]

        search = local_client.post(
            "/api/v1/context/catalog/search",
            json={**scope, "query": "memory", "root_uri": root},
        )
        assert search.status_code == 200
        assert search.json()["backend"] == "catalog-hierarchical-v1"

        item = local_client.post(
            "/api/v1/context/catalog/item",
            json={**scope, "uri": root},
        )
        assert item.status_code == 200
        assert item.json()["item"]["kind"] == "directory"

        foreign = local_client.post(
            "/api/v1/context/catalog/item",
            json={
                **scope,
                "uri": root.replace("tenant-1", "tenant-2"),
            },
        )
        assert foreign.status_code == 422
        assert foreign.json()["runtime_error"]["code"] == "SCOPE_ERROR"

        reindex = local_client.post(
            "/api/v1/context/catalog/reindex",
            json={**scope, "root_uri": root, "layers": ["L0", "L1"]},
        )
        assert reindex.status_code == 200
        assert reindex.json()["root_uri"] == root
        assert reindex.json()["scanned_items"] >= 1
        assert reindex.json()["indexed_layers"] >= 0


@pytest.mark.unit
def test_retrieval_trace_can_be_read_with_scope() -> None:
    app = create_app(AppSettings(profile="demo", memory_store="sqlite"))
    scope = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "agent_id": "agent-1",
        "session_id": "session-1",
    }
    with TestClient(app) as local_client:
        response = local_client.post(
            "/api/v1/context",
            json={**scope, "query": "context trace", "memory_types": ["working"]},
        )
        assert response.status_code == 200
        trace_id = response.json()["trace_id"]

        trace = local_client.get(
            f"/api/v1/context/traces/{trace_id}",
            params=scope,
        )
        assert trace.status_code == 200
        assert trace.json()["trace_id"] == trace_id
        assert trace.json()["scope"]["tenant_id"] == "tenant-1"

        foreign = local_client.get(
            f"/api/v1/context/traces/{trace_id}",
            params={**scope, "tenant_id": "tenant-2"},
        )
        assert foreign.status_code == 422
        assert foreign.json()["runtime_error"]["code"] == "SCOPE_ERROR"

        missing_session = local_client.get(
            f"/api/v1/context/traces/{trace_id}",
            params={key: value for key, value in scope.items() if key != "session_id"},
        )
        assert missing_session.status_code == 422
        assert missing_session.json()["runtime_error"]["code"] == "SCOPE_ERROR"


@pytest.mark.unit
def test_resource_registration_creates_p3_owned_context_resource() -> None:
    app = create_app(AppSettings(profile="demo", memory_store="sqlite"))
    scope = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "agent_id": "agent-1",
        "session_id": "session-1",
    }
    uri = (
        "aether://tenants/tenant-1/users/user-1/agents/agent-1/"
        "resources/documents/design-notes"
    )
    with TestClient(app) as local_client:
        response = local_client.post(
            "/api/v1/context/resources",
            json={
                **scope,
                "resource_id": "design-notes",
                "name": "Design notes",
                "description": "Architecture notes",
                "content": "The P3 Context Catalog owns resource identity.",
            },
        )
        assert response.status_code == 200
        assert response.json()["resource"]["status"] == "ready"
        assert response.json()["resource"]["metadata"]["content_authority"] == "inline"

        item = local_client.post(
            "/api/v1/context/catalog/item",
            json={**scope, "uri": uri},
        )
        assert item.status_code == 200
        assert item.json()["item"]["kind"] == "resource"
        assert item.json()["item"]["layers"]["L2"]["text"].startswith("The P3")

        deletion = local_client.post(
            "/api/v1/context/resources/design-notes/delete",
            json=scope,
        )
        assert deletion.status_code == 200
        assert deletion.json()["deletion"]["deleted"] is True

        deleted_item = local_client.post(
            "/api/v1/context/catalog/item",
            json={**scope, "uri": uri},
        )
        assert deleted_item.status_code == 200
        assert deleted_item.json()["item"] is None


@pytest.mark.unit
def test_skill_registration_creates_agent_context_skill() -> None:
    app = create_app(AppSettings(profile="demo", memory_store="sqlite"))
    scope = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "agent_id": "agent-1",
        "session_id": "session-1",
    }
    uri = (
        "aether://tenants/tenant-1/users/user-1/agents/agent-1/"
        "skills/context-review"
    )
    with TestClient(app) as local_client:
        response = local_client.post(
            "/api/v1/context/skills",
            json={
                **scope,
                "skill_id": "context-review",
                "name": "Context review",
                "description": "Review recalled context.",
                "instructions": "Reject unsupported context before responding.",
                "version": "2",
            },
        )
        assert response.status_code == 200
        assert response.json()["skill"]["version"] == "2"
        assert response.json()["skill"]["scope"]["session_id"] is None

        item = local_client.post(
            "/api/v1/context/catalog/item",
            json={**scope, "uri": uri},
        )
        assert item.status_code == 200
        assert item.json()["item"]["kind"] == "skill"
        assert (
            item.json()["item"]["layers"]["L2"]["text"]
            == "Reject unsupported context before responding."
        )

        deletion = local_client.post(
            "/api/v1/context/skills/context-review/delete",
            json=scope,
        )
        assert deletion.status_code == 200
        assert deletion.json()["deletion"]["deleted"] is True


@pytest.mark.unit
def test_session_routes_commit_archive_into_context_catalog() -> None:
    app = create_app(AppSettings(profile="demo", memory_store="sqlite"))
    scope = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "agent_id": "agent-1",
        "session_id": "session-1",
    }
    session_root = (
        "aether://tenants/tenant-1/users/user-1/agents/agent-1/"
        "sessions/session-1"
    )
    with TestClient(app) as local_client:
        first = local_client.post(
            "/api/v1/sessions/messages",
            json={**scope, "role": "user", "content": "remember this decision"},
        )
        second = local_client.post(
            "/api/v1/sessions/messages",
            json={**scope, "role": "assistant", "content": "the decision was recorded"},
        )
        assert first.status_code == 200
        assert second.status_code == 200

        commit = local_client.post(
            "/api/v1/sessions/commit",
            json={**scope, "keep_recent_count": 0},
        )
        assert commit.status_code == 200
        assert commit.json()["status"] == "committed"
        assert commit.json()["archived_messages"] == 2

        consolidation = local_client.post(
            "/api/v1/sessions/consolidate",
            json={**scope, "archive_id": commit.json()["archive_id"]},
        )
        repeat = local_client.post(
            "/api/v1/sessions/consolidate",
            json={**scope, "archive_id": commit.json()["archive_id"]},
        )
        assert consolidation.status_code == 200
        assert repeat.status_code == 200
        assert consolidation.json()["status"] == "succeeded"
        assert repeat.json()["memory_id"] == consolidation.json()["memory_id"]

        current = local_client.post("/api/v1/sessions/current", json=scope)
        assert current.status_code == 200
        assert current.json()["session"]["messages"] == []

        history = local_client.post(
            "/api/v1/context/catalog/children",
            json={**scope, "parent_uri": f"{session_root}/history"},
        )
        assert history.status_code == 200
        assert len(history.json()["items"]) == 1
        assert history.json()["items"][0]["layers"]["L0"]["status"] == "available"
        assert (
            history.json()["items"][0]["metadata"]["memory_extraction_status"]
            == "succeeded"
        )


@pytest.mark.unit
def test_create_app_uses_passed_settings_not_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AETHER_P2_GRPC", "env-p2:9999")
    monkeypatch.setenv("AETHER_B1_SIDECAR_URL", "http://env-b1:18081")
    monkeypatch.setenv("AETHER_B1_EMBEDDING_URL", "http://env-b1:18081/v1/intercept")
    monkeypatch.setenv("AETHER_B2_BROKER_URL", "redis://env-broker:6379/0")
    monkeypatch.setenv("AETHER_B2_TASK_STATUS_URL", "redis://env-task:6379/0")
    settings = AppSettings(
        profile="demo",
        data_dir=str(tmp_path),
        p2_endpoint="settings-p2:1234",
        p2_engine="settings-engine",
        p2_bucket="settings-bucket",
        p2_collection="settings-collection",
        redis_url="redis://settings-redis:6379/0",
        b1_sidecar_url="http://settings-b1:18081",
        b1_embedding_url="http://settings-b1:18081/v1/intercept",
        b1_model_name="settings-model",
        broker_url="redis://settings-broker:6379/0",
        task_status_url="redis://settings-task:6379/0",
        retrieval_trace_ttl_seconds=7200,
        retrieval_trace_max_entries=250,
        retrieval_trace_timeout_seconds=0.5,
    )

    app = create_app(settings)
    with TestClient(app) as test_client:
        runtime = test_client.app.state.runtime
        config = runtime.legacy_runtime.config
        vector_search = runtime.dependencies.vector_search
        task_queue = runtime.dependencies.task_queue
        task_status = runtime.dependencies.task_status

    assert config.p2_endpoint == "settings-p2:1234"
    assert config.p2_engine == "settings-engine"
    assert config.p2_bucket == "settings-bucket"
    assert config.p2_collection == "settings-collection"
    assert config.redis_url == "redis://settings-redis:6379/0"
    assert config.b1_sidecar_url == "http://settings-b1:18081"
    assert config.b1_embedding_url == "http://settings-b1:18081/v1/intercept"
    assert config.b1_model_name == "settings-model"
    assert config.broker_url == "redis://settings-broker:6379/0"
    assert config.task_status_url == "redis://settings-task:6379/0"
    assert config.retrieval_trace_ttl_seconds == 7200
    assert config.retrieval_trace_max_entries == 250
    assert config.retrieval_trace_timeout_seconds == 0.5
    assert vector_search is not None
    assert task_queue is not None
    assert task_status is not None
    assert vector_search._b1_endpoint == "http://settings-b1:18081/v1/intercept"
    assert task_queue._broker_url == "redis://settings-broker:6379/0"
    assert task_status._redis_url == "redis://settings-task:6379/0"


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
