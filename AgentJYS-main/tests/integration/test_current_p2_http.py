"""HTTP/Temporal regression against real Azure PG, Redis, Milvus and Ceph.

The model is controlled for fault assertions; the separate real-model gate
checks BGE and LLM. Neither suite certifies production Temporal or HA.
"""

import time
from hashlib import sha256
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from aether_agent_memory.runtime.contracts.models import Permission
from azure_component_service import Service
from component_configuration import ComponentConfiguration

pytestmark = pytest.mark.integration


def headers(operation=None, user="alice"):
    value = {"Authorization": "Bearer " + user}
    if operation:
        value["X-Operation-ID"] = operation
    return value


def eventually(check):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("current P2 business flow did not converge")


def command(client, path, body, operation):
    response = client.post(path, json=body, headers=headers(operation))
    location = response.headers.get("Location")
    assert location and location.startswith("/p3/operations/"), response.text
    deadline = time.monotonic() + 90
    while response.status_code == 400 and response.json().get("code") == "REQUEST_IN_PROGRESS":
        assert time.monotonic() < deadline, response.text
        time.sleep(0.03)
        # Poll the accepted job; never repost an ambiguous mutation.
        response = client.get(location + "/result", headers=headers())
    assert response.status_code == 200, response.text
    return response.json(), location


@pytest.fixture
def configuration(tmp_path, temporal_server):
    identity = tmp_path / "identities.yaml"
    identity.write_text(
        yaml.safe_dump(
            {
                "revision": 1,
                "tenants": [{"tenant_id": "t1"}, {"tenant_id": "t2"}],
                "identities": [
                    {
                        "credential_sha256": sha256(user.encode()).hexdigest(),
                        "principal": {
                            "principal_id": user,
                            "auth_epoch": 1,
                            "permissions": [permission.value for permission in Permission],
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
    namespace = "p3-http-" + uuid4().hex
    return ComponentConfiguration(
        data_dir=tmp_path / "state",
        identity_file=identity,
        embedding_profile="injected",
        remember={"consolidation_messages": 1},
        periodic_seconds=0.2,
        poll_seconds=0.02,
        shutdown_seconds=1,
        http_wait_seconds=0.05,
        temporal={"deployment_id": namespace, "endpoint": temporal_server.endpoint},
    )


def test_component_start_prepares_owned_azure_vectors_for_real_readiness(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        health = client.portal.call(
            service.runtime.health.report, service.runtime.foundation.identity.context("alice")
        )
        assert health["dependencies"]["vectors"]["state"] == "available"
        assert client.get("/p3/ready", headers=headers()).status_code == 200


@pytest.mark.parametrize("path", ["/p3/health", "/p3/ready"])
def test_http_health_snapshot_waits_leave_transport_callbacks_runnable(
    configuration, monkeypatch, path
):
    import asyncio
    from threading import Event

    service = Service(configuration)
    with TestClient(service.app()) as client:
        loop = client.portal.call(asyncio.get_running_loop)
        original = service.runtime.foundation.monitoring.health
        responsive = []

        def delayed_snapshot(ctx):
            released = Event()
            loop.call_soon_threadsafe(released.set)
            responsive.append(released.wait(0.3))
            return original(ctx)

        monkeypatch.setattr(service.runtime.foundation.monitoring, "health", delayed_snapshot)
        response = client.get(path, headers=headers())
        assert response.status_code == 200, response.text
        assert response.json()["schema_version"] == "p3/health/2"
        assert responsive and all(responsive), "HTTP health snapshot blocked transport callbacks"


def test_azure_http_save_recall_restart_correct_and_delete(configuration):
    body = {
        "source": {
            "kind": "conversation",
            "external_id": "source",
            "external_version": "1",
            "occurred_at": "2026-10-02T00:00:00.000Z",
        },
        "selection": {"session_id": "s1"},
        "content": {"kind": "text", "text": "I prefer unsweetened coffee."},
    }
    recall = {
        "query": "unsweetened coffee",
        "selection": {},
        "sources": "long_term",
        "token_budget": 1000,
    }
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        catalog = client.get("/p3/capabilities", headers=headers()).json()
        assert catalog["object_storage"] == "ceph"
        assert catalog["production_ready"] is False
        saved, saved_location = command(client, "/p3/remember", body, "save1")
        repeated, repeated_location = command(client, "/p3/remember", body, "save1")
        assert repeated == saved and repeated_location == saved_location

        def ready():
            response = client.get("/p3/memories", headers=headers())
            assert response.status_code == 200, response.text
            return next(
                (
                    item
                    for item in response.json()["items"]
                    if item["kind"] != "working" and item["projection_state"] == "ready"
                ),
                None,
            )

        memory = eventually(ready)
        memory_id = memory["ref"]["memory_id"]
        old, _ = command(client, "/p3/recall", recall, "recall1")
        assert "unsweetened coffee" in old["rendered_context"]
        assert (
            client.get(f"/p3/remember/{memory_id}", headers=headers(user="eve")).status_code == 403
        )
        with service.runtime.foundation.uow.transaction() as tx:
            projections = tx.rows(service.runtime.vectors.projection_namespace)
        assert projections, "business processing must have published real Milvus projection"
        assert not list((configuration.data_dir / "bodies").glob("*"))
        assert not list((configuration.data_dir / "inputs").glob("*"))

    with TestClient(Service(configuration).app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        result = client.get(saved_location + "/result", headers=headers())
        assert result.status_code == 200 and result.json() == saved
        result = client.get(f"/p3/recalls/{old['recall_id']}/result", headers=headers())
        assert result.status_code == 200 and result.json() == old
        command(
            client,
            f"/p3/remember/{memory_id}/correct",
            {
                "expected_version": 1,
                "content": "I prefer green tea now.",
                "reason": "user correction",
                "source": {**body["source"], "external_id": "correction"},
            },
            "correct1",
        )
        assert (
            client.get(f"/p3/recalls/{old['recall_id']}/result", headers=headers()).status_code
            == 410
        )

        def revised():
            response = client.get(f"/p3/remember/{memory_id}", headers=headers())
            assert response.status_code == 200, response.text
            item = response.json()
            return (
                item
                if item["ref"]["version"] == 2 and item["projection_state"] == "ready"
                else None
            )

        current = eventually(revised)
        new, _ = command(client, "/p3/recall", {**recall, "query": "green tea"}, "recall2")
        assert "green tea" in new["rendered_context"]
        assert "unsweetened coffee" not in new["rendered_context"]
        deleted = client.post(
            f"/p3/remember/{memory_id}/delete",
            headers=headers("delete1"),
            json={
                "expected_revision": current["object_revision"],
                "reason": "user deletion",
            },
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["blocked"]
        result, _ = command(client, "/p3/recall", {**recall, "query": "green tea"}, "recall3")
        assert result["outcome"] == "empty" and "green tea" not in result["rendered_context"]
        assert (
            client.get(f"/p3/recalls/{new['recall_id']}/result", headers=headers()).status_code
            == 410
        )
        for task_id in deleted.json()["task_ids"]:

            def cleaned(task_id=task_id):
                status = client.get(f"/p3/tasks/{task_id}", headers=headers())
                assert status.status_code == 200, status.text
                task = status.json()
                assert task["state"] not in {"failed", "attention_required"}, task
                return task["state"] == "succeeded"

            eventually(cleaned)
        working, _ = command(
            client,
            "/p3/recall",
            {
                **recall,
                "sources": "working",
                "selection": {"session_id": "s1"},
            },
            "working_after_delete",
        )
        assert "unsweetened coffee" in working["rendered_context"]
