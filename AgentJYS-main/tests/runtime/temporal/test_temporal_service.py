import pytest
from fastapi.testclient import TestClient
from test_temporal_http import configuration, wait_ready

from azure_component_service import Service


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


def test_stopped_worker_host_is_rebuilt_and_same_operation_survives(tmp_path, temporal_server):
    from test_temporal_http import headers, request, wait_result

    service = Service(configuration(tmp_path, temporal_server.endpoint))
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        response = client.post("/p3/remember", json=request(), headers=headers())
        if response.status_code == 400:
            result = wait_result(client, response.headers["location"])
        else:
            result = response
        assert result.status_code == 200, result.text
        original = result.json()
        old_host = service.execution.workers
        assert old_host is not None
        assert client.portal is not None
        client.portal.call(old_host.stop)
        client.portal.call(service.execution.refresh)
        new_host = service.execution.workers
        assert new_host is not None and new_host is not old_host
        assert new_host.runners and not any(runner.done() for runner in new_host.runners)
        wait_ready(client, service)
        repeated = client.post("/p3/remember", json=request(), headers=headers())
        if repeated.status_code == 400:
            repeated = wait_result(client, repeated.headers["location"])
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == original


def test_failed_worker_runner_is_rebuilt_with_error_observed(tmp_path, temporal_server):
    import asyncio

    service = Service(configuration(tmp_path, temporal_server.endpoint))
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        old_host = service.execution.workers
        assert old_host is not None and client.portal is not None

        async def inject_failure():
            async def failed():
                raise RuntimeError("injected worker runner failure")

            task = asyncio.create_task(failed())
            await asyncio.sleep(0)
            old_host.runners.append(task)
            await service.execution.refresh()

        client.portal.call(inject_failure)
        assert service.execution.workers is not old_host
        wait_ready(client, service)
        assert all(not runner.done() for runner in service.execution.workers.runners)
