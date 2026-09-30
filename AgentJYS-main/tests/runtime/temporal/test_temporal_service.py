import pytest
from fastapi.testclient import TestClient
from test_temporal_http import configuration, wait_ready

from aether_agent_memory.runtime.flows.application import Service


def test_temporal_unavailable_is_visible_without_liveness_failure(tmp_path):
    service = Service(configuration(tmp_path, "127.0.0.1:1"))
    with TestClient(service.app()) as client:
        live = client.get("/p3/live")
        ready = client.get("/p3/readyz")
        assert live.status_code == 200
        assert ready.status_code == 503
        assert ready.json()["reason_code"] == "TEMPORAL_UNAVAILABLE"


def test_second_service_refuses_same_directory_before_loading_runtime(tmp_path, temporal_server):
    config = configuration(tmp_path, temporal_server.endpoint)
    service = Service(config)
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            Service(config)
    finally:
        import asyncio

        asyncio.run(service.close())


def test_retired_manual_cycle_does_not_report_fictitious_scheduled_work(tmp_path, temporal_server):
    service = Service(configuration(tmp_path, temporal_server.endpoint))
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        response = client.post("/p3/maintenance/cycle", headers={"Authorization": "Bearer alice"})
        assert response.status_code == 410
        assert "scheduled" not in response.json()
