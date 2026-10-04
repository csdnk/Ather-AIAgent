import asyncio
import hashlib
import importlib.util
import threading
import time
from uuid import uuid4

import yaml
from fastapi.testclient import TestClient

from aether_agent_memory.runtime.contracts.models import Permission
from azure_component_service import Service
from component_configuration import ComponentConfiguration as ServiceConfiguration


def configuration(tmp_path, endpoint):
    assert importlib.util.find_spec("aether_agent_memory.runtime.temporal.service"), (
        "unified service has no Temporal facade"
    )
    identity = tmp_path / "identities.yaml"
    identity.write_text(
        yaml.safe_dump(
            {
                "revision": 1,
                "tenants": [{"tenant_id": "t1"}, {"tenant_id": "t2"}],
                "identities": [
                    {
                        "credential_sha256": hashlib.sha256(user.encode()).hexdigest(),
                        "principal": {
                            "principal_id": user,
                            "auth_epoch": 1,
                            "permissions": [p.value for p in Permission],
                            "home_scope": {
                                "tenant_id": tenant,
                                "application_id": "app",
                                "user_id": user,
                                "agent_id": user,
                            },
                        },
                    }
                    for user, tenant in (("alice", "t1"), ("eve", "t2"))
                ],
            }
        ),
        encoding="utf-8",
    )
    return ServiceConfiguration(
        data_dir=tmp_path / "state",
        identity_file=identity,
        embedding_profile="injected",
        periodic_seconds=0.2,
        poll_seconds=0.02,
        identity_reload_seconds=0.1,
        shutdown_seconds=1,
        http_wait_seconds=0.05,
        temporal={
            "deployment_id": uuid4().hex,
            "endpoint": endpoint,
            "connect_timeout_seconds": 0.3,
        },
    )


def headers(user="alice"):
    return {"Authorization": "Bearer " + user, "X-Operation-ID": "same-command"}


def test_capability_catalog_reports_current_p2_without_direct_store_fields(
    tmp_path, temporal_server
):
    config = configuration(tmp_path, temporal_server.endpoint)
    # No legacy Ceph field exists on the consumer's deployment configuration.
    assert not hasattr(config, "ceph")
    service = Service(config)
    with TestClient(service.app(), raise_server_exceptions=False) as client:
        response = client.get("/p3/capabilities", headers=headers())
        assert response.status_code == 200, response.text
        capabilities = response.json()
        assert capabilities["storage_mode"] == "current_p2"
        assert capabilities["production_ready"] is False
        assert capabilities["object_storage"] == "local_reference"


def request():
    return {
        "source": {
            "kind": "conversation",
            "external_id": "source",
            "external_version": "1",
            "occurred_at": "2026-09-30T00:00:00.000Z",
        },
        "selection": {"session_id": "s1"},
        "content": {"kind": "text", "text": "A confirmed durable memory."},
    }


def wait_result(client, url):
    end = time.monotonic() + 60
    while time.monotonic() < end:
        response = client.get(url + "/result", headers=headers())
        if response.status_code != 400:
            return response
        assert response.json()["code"] == "REQUEST_IN_PROGRESS", response.text
        time.sleep(0.03)
    raise AssertionError("Temporal result did not commit")


def wait_ready(client, service):
    # API startup is intentionally allowed while Temporal is reconnecting.
    end = time.monotonic() + 60
    while time.monotonic() < end:
        if client.get("/p3/readyz").status_code == 200:
            return
        time.sleep(0.03)
    raise AssertionError(service.execution.state)


def test_http_wait_timeout_preserves_workflow(tmp_path, temporal_server, monkeypatch):
    service = Service(configuration(tmp_path, temporal_server.endpoint))
    release = threading.Event()
    persist = service.runtime.remember.bodies.persist

    async def blocked(*args, **kwargs):
        while not release.is_set():
            await asyncio.sleep(0.02)
        return await persist(*args, **kwargs)

    monkeypatch.setattr(service.runtime.remember.bodies, "persist", blocked)
    workflow_cancel_count = 0
    from temporalio.client import WorkflowHandle

    original_cancel = WorkflowHandle.cancel

    async def counted_cancel(*args, **kwargs):
        nonlocal workflow_cancel_count
        workflow_cancel_count += 1
        return await original_cancel(*args, **kwargs)

    monkeypatch.setattr(WorkflowHandle, "cancel", counted_cancel)
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        response = client.post("/p3/remember", json=request(), headers=headers())
        assert response.status_code == 400, (response.text, service.execution.state)
        assert response.json()["code"] == "REQUEST_IN_PROGRESS"
        location = response.headers["location"]
        assert location.startswith("/p3/operations/")
        assert response.headers["x-p3-job-id"] != "same-command"
        release.set()
        result = wait_result(client, location)
        assert result.status_code == 200, result.text
        repeated = client.post("/p3/remember", json=request(), headers=headers())
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == result.json()
        assert workflow_cancel_count == 0
        assert client.get("/p3/readyz").status_code == 200
        assert client.get("/p3/runtime", headers=headers()).json()["worker_state"] == "available"


def test_cross_tenant_operation_is_forbidden(tmp_path, temporal_server):
    service = Service(configuration(tmp_path, temporal_server.endpoint))
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        response = client.post("/p3/remember", json=request(), headers=headers())
        assert response.status_code in {200, 400}, (response.text, service.execution.state)
        location = response.headers["location"]
        result = wait_result(client, location)
        assert result.status_code == 200, result.text
        for suffix in ("", "/result"):
            response = client.get(location + suffix, headers=headers("eve"))
            assert response.status_code == 403, response.text
        with service.runtime.foundation.uow.transaction() as tx:
            row = tx.read("identities", "alice")
            tx.write("identities", "alice", {**row, "enabled": False})
        assert client.get(location + "/result", headers=headers()).status_code in {401, 403}


def test_full_http_commands_use_temporal_and_probes_do_not_write_p2(
    tmp_path, temporal_server, monkeypatch
):
    service = Service(configuration(tmp_path, temporal_server.endpoint))
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        saved = client.post("/p3/remember", json=request(), headers=headers())
        assert saved.status_code in {200, 400}, (saved.text, service.execution.state)
        receipt = wait_result(client, saved.headers["location"])
        assert receipt.status_code == 200, receipt.text
        memory_id = receipt.json()["memories"][0]["memory_id"]
        current = client.get(f"/p3/remember/{memory_id}", headers=headers()).json()
        correct = client.post(
            f"/p3/remember/{memory_id}/correct",
            json={
                "expected_version": current["ref"]["version"],
                "content": "Correct durable memory.",
                "source": {**request()["source"], "external_id": "correction"},
                "reason": "correction",
            },
            headers={**headers(), "X-Operation-ID": "correct"},
        )
        assert correct.status_code in {200, 400}, correct.text
        corrected = wait_result(client, correct.headers["location"])
        assert corrected.status_code == 200, corrected.text
        recall = client.post(
            "/p3/recall",
            json={"query": "durable memory", "sources": "long_term", "selection": {}},
            headers={**headers(), "X-Operation-ID": "recall"},
        )
        assert recall.status_code in {200, 400}, recall.text
        packed = wait_result(client, recall.headers["location"])
        assert packed.status_code == 200, packed.text
        assert "recall_id" in packed.json()
        document = client.put(
            "/p3/documents/doc?version=1",
            content=b"Durable document.",
            headers={**headers(), "X-Operation-ID": "document", "Content-Type": "text/plain"},
        )
        assert document.status_code in {200, 400}, document.text
        uploaded = wait_result(client, document.headers["location"])
        assert uploaded.status_code == 200, uploaded.text

        def forbidden_write(*args, **kwargs):
            raise AssertionError("readiness probe must not write P2")

        monkeypatch.setattr(service.runtime.remember.bodies.p2, "put_object", forbidden_write)
        assert client.get("/p3/readyz").status_code == 200
        assert client.get("/p3/ready", headers=headers()).status_code == 200


def test_command_result_refuses_damaged_completion_receipt(tmp_path, temporal_server):
    service = Service(configuration(tmp_path, temporal_server.endpoint))
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        response = client.post("/p3/remember", json=request(), headers=headers())
        assert response.status_code in {200, 400}, (response.text, service.execution.state)
        location = response.headers["location"]
        assert wait_result(client, location).status_code == 200
        job_id = response.headers["x-p3-job-id"]
        with service.runtime.foundation.uow.transaction() as tx:
            _, task = service.execution.ledger.tasks.load(tx, job_id)
            value = tx.get(task.result_ref)
            tx.put_if_revision(
                task.result_ref, {**value, "memories": []}, tx.revision(task.result_ref)
            )
        assert client.get(location + "/result", headers=headers()).status_code == 409
