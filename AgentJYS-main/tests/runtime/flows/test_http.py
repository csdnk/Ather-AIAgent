from hashlib import sha256

from fastapi.testclient import TestClient

from aether_agent_memory.operate.basic.continuous import ContinuousOperate
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.flows.http import create_app
from aether_agent_memory.runtime.foundation.common import now
from azure_test_runtime import create_runtime
from temporal_test_support import http_execution


def test_http_uses_shared_services_auth_scoping_and_health(tmp_path, temporal_server):
    runtime = create_runtime(
        tmp_path / "http.db",
        tmp_path / "cache",
        embedding_profile="injected",
        operate_factory=ContinuousOperate,
    )
    people = [
        Principal(
            principal_id=name,
            home_scope=Scope(
                tenant_id=tenant, application_id="app", user_id=name, agent_id="agent"
            ),
            permissions=tuple(Permission),
            auth_epoch=1,
        )
        for name, tenant in (("alice", "t1"), ("bob", "t2"))
    ]
    runtime.foundation.identity.provision(
        [(sha256(p.principal_id.encode()).hexdigest(), p) for p in people]
    )
    http_execution(runtime, temporal_server.endpoint)
    try:
        with TestClient(create_app(runtime, run_worker=False)) as client:
            assert client.get("/p3/live").status_code == 200
            assert client.get("/p3/health").status_code == 401
            headers = {"Authorization": "Bearer alice", "X-Operation-ID": "save_http"}
            payload = {
                "selection": {"session_id": "http_session"},
                "source": {
                    "kind": "text",
                    "external_id": "http_input",
                    "external_version": "1",
                    "occurred_at": now(),
                },
                "content": {"kind": "text", "text": "项目预算为30万元"},
            }
            saved = client.post("/p3/remember", json=payload, headers=headers)
            assert saved.status_code == 200, saved.text
            assert client.post("/p3/remember", json=payload, headers=headers).json() == saved.json()
            task_id = saved.json()["task_ids"][0]
            assert (
                client.get(
                    f"/p3/tasks/{task_id}", headers={"Authorization": "Bearer bob"}
                ).status_code
                == 403
            )
            client.portal.call(runtime.drain)
            task = client.get(f"/p3/tasks/{task_id}", headers=headers)
            assert task.json()["state"] == "succeeded"
            progress = client.get(f"/p3/tasks/{task_id}/progress", headers=headers).json()
            assert progress["checkpoints"][0]["stage"] == "completed"
            recall = client.post(
                "/p3/recall",
                json={
                    "query": "预算",
                    "selection": {"session_id": "http_session"},
                    "sources": "working",
                    "token_budget": 200,
                },
                headers={"Authorization": "Bearer alice", "X-Operation-ID": "recall_http"},
            )
            assert recall.status_code == 200, recall.text
            assert "30" in recall.json()["rendered_context"]
            health = client.get("/p3/health", headers=headers)
            assert health.status_code == 200, health.text
            assert health.json()["schema_version"] == "p3/health/2"
            assert health.json()["production_acceptance"] is False
            assert (
                client.post(
                    "/p3/backups", json={"backup_id": "unauthorized"}, headers=headers
                ).status_code
                == 403
            )
    finally:
        runtime.close()


def test_http_worker_runs_queued_tasks_and_stops_on_shutdown(tmp_path, temporal_server):
    import time

    runtime = create_runtime(
        tmp_path / "worker.db",
        tmp_path / "cache",
        embedding_profile="injected",
        operate_factory=ContinuousOperate,
    )
    person = Principal(
        principal_id="alice",
        home_scope=Scope(tenant_id="t", application_id="app", user_id="alice", agent_id="agent"),
        permissions=tuple(Permission),
        auth_epoch=1,
    )
    runtime.foundation.identity.provision([(sha256(b"alice").hexdigest(), person)])
    http_execution(runtime, temporal_server.endpoint)
    try:
        with TestClient(create_app(runtime, maintenance_credential=lambda: "revoked")) as client:
            headers = {"Authorization": "Bearer alice"}
            response = client.post(
                "/p3/remember",
                headers=headers,
                json={
                    "selection": {},
                    "source": {
                        "kind": "text",
                        "external_id": "worker",
                        "external_version": "1",
                        "occurred_at": now(),
                    },
                    "content": {"kind": "text", "text": "后台工作"},
                },
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task_ids"][0]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                task = client.get(f"/p3/tasks/{task_id}", headers=headers).json()
                if task["state"] == "succeeded":
                    break
                time.sleep(0.02)
            assert task["state"] == "succeeded"
            assert client.get("/p3/runtime", headers=headers).json()["http_worker"] == "running"
    finally:
        runtime.close()
