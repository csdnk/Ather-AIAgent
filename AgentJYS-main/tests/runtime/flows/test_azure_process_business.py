"""Actual HTTP, real Azure stores/models and exact child-process restart.

Static test identities and a test-owned development Temporal are explicit boundaries.
"""

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml
from test_azure_storage_assembly import configured
from test_postgres_observability import dsns as dsns

from aether_agent_memory.runtime.contracts.models import Permission
from aether_agent_memory.runtime.flows.config import LanguageModel
from aether_agent_memory.runtime.storage.configuration import CephStorageConfiguration
from azure_storage_support import azure_redis as azure_redis

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("P3_REQUIRE_REAL_MODEL") != "1" or os.environ.get("P3_REQUIRE_CEPH") != "1",
        reason="explicit real Azure/model/Ceph process acceptance required",
    ),
]


def wait_for(check, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("real Azure process business did not converge")


def test_real_http_restarts_original_job_then_corrects_and_deletes(
    tmp_path, dsns, azure_redis, temporal_server, monkeypatch
):
    config = configured(tmp_path, dsns, azure_redis, monkeypatch)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = config.model_copy(
        update={
            "host": "127.0.0.1",
            "port": port,
            "language_model": LanguageModel(
                endpoint=os.environ["P3_TEST_LLM_ENDPOINT"],
                model=os.environ["P3_TEST_LLM_MODEL"],
                api_key_env="P3_TEST_LLM_KEY",
                response_format="json_schema",
                timeout_seconds=90,
            ),
            "embedding_config": Path(os.environ["P3_TEST_NATIVE_CONFIG"]),
            "azure_storage": config.azure_storage.model_copy(
                update={
                    "ceph": CephStorageConfiguration(
                        endpoint=os.environ["P3_TEST_CEPH_ENDPOINT"],
                        bucket=os.environ["P3_TEST_CEPH_BUCKET"],
                        access_key_env="P3_TEST_CEPH_ACCESS",
                        secret_key_env="P3_TEST_CEPH_SECRET",
                        allow_insecure_http=os.environ.get("P3_TEST_CEPH_ALLOW_HTTP") == "1",
                    )
                }
            ),
            "remember": config.remember.model_copy(update={"consolidation_messages": 1}),
            "temporal": config.temporal.model_copy(
                update={
                    "endpoint": temporal_server.endpoint,
                    "deployment_id": config.azure_storage.namespace,
                }
            ),
            "periodic_seconds": 1,
            "poll_seconds": 0.2,
            "http_wait_seconds": 0.01,
            "shutdown_seconds": 3,
        }
    )
    config.identity_file.write_text(
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
                            "permissions": [permission.value for permission in Permission],
                            "home_scope": {
                                "tenant_id": tenant,
                                "application_id": "app",
                                "user_id": user,
                                "agent_id": "agent",
                            },
                        },
                    }
                    for user, tenant in (("alice", "t1"), ("eve", "t2"))
                ],
            }
        ),
        encoding="utf8",
    )
    path = tmp_path / "service.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf8")
    root = Path(__file__).resolve().parents[3]
    children, logs = [], []
    evidence = {"boundary": "real Azure/model HTTP; static identities; development Temporal"}

    def start(phase):
        log = (tmp_path / (phase + ".log")).open("wb")
        logs.append(log)
        child = subprocess.Popen(
            [sys.executable, "-B", "-m", "aether_agent_memory", "serve", "--config", str(path)],
            cwd=root,
            env=os.environ.copy(),
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        children.append(child)

        def ready():
            assert child.poll() is None, "P3 child exited; inspect test-owned process log"
            try:
                return client.get("/p3/readyz").status_code == 200
            except httpx.TransportError:
                return False

        wait_for(ready)
        return child

    def headers(operation=None, user="alice"):
        value = {"Authorization": "Bearer " + user}
        if operation:
            value["X-Operation-ID"] = operation
        return value

    def original_result(location):
        response = client.get(location + "/result", headers=headers())
        if response.status_code == 400 and response.json().get("code") == "REQUEST_IN_PROGRESS":
            return None
        assert response.status_code == 200, response.text
        return response.json()

    def command(route, body, operation):
        response = client.post(route, json=body, headers=headers(operation))
        location = response.headers.get("Location")
        assert location and location.startswith("/p3/operations/"), response.text
        assert response.status_code in {200, 400}, response.text
        return wait_for(lambda: original_result(location)), location

    def catalog():
        response = client.get("/p3/memories", headers=headers())
        assert response.status_code == 200, response.text
        return response.json()["items"]

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False) as client:
        try:
            first = start("first")
            request = {
                "source": {
                    "kind": "conversation",
                    "external_id": "process-source",
                    "external_version": "1",
                    "occurred_at": "2026-10-04T00:00:00.000Z",
                },
                "selection": {"task_id": "process-task", "session_id": "original-session"},
                "content": {"kind": "text", "text": "赵明只喝不加糖的红茶。"},
            }
            submitted = client.post("/p3/remember", json=request, headers=headers("process-save"))
            location = submitted.headers.get("Location")
            assert location and location.startswith("/p3/operations/"), submitted.text
            assert (
                submitted.status_code == 400 and submitted.json()["code"] == "REQUEST_IN_PROGRESS"
            )
            evidence["original_job"] = location
            first.kill()
            first.wait(timeout=10)
            second = start("resume")
            saved = wait_for(lambda: original_result(location))
            repeated, replay_location = command("/p3/remember", request, "process-save")
            assert repeated == saved and replay_location == location
            item = wait_for(
                lambda: next(
                    (
                        item
                        for item in catalog()
                        if item["kind"] == "semantic" and item["projection_state"] == "ready"
                    ),
                    None,
                )
            )
            memory_id = item["ref"]["memory_id"]
            query = {
                "query": "赵明喝什么茶，加糖吗？",
                "selection": {"task_id": "process-task"},
                "sources": "long_term",
                "token_budget": 1000,
            }
            old, old_location = command("/p3/recall", query, "process-recall")
            assert "红茶" in old["rendered_context"]
            assert (
                client.get(f"/p3/remember/{memory_id}", headers=headers(user="eve")).status_code
                == 403
            )
            correction = {
                "expected_version": item["ref"]["version"],
                "content": "赵明改为只喝不加糖的绿茶。",
                "source": {**request["source"], "external_id": "process-correction"},
                "reason": "explicit user correction",
            }
            corrected, correction_location = command(
                f"/p3/remember/{memory_id}/correct", correction, "process-correct"
            )
            replay, replay_location = command(
                f"/p3/remember/{memory_id}/correct", correction, "process-correct"
            )
            assert corrected == replay and correction_location == replay_location

            def corrected_ready():
                current = client.get(f"/p3/remember/{memory_id}", headers=headers()).json()
                return (
                    current
                    if current.get("projection_state") == "ready" and current["ref"]["version"] == 2
                    else None
                )

            wait_for(corrected_ready)
            updated, _ = command("/p3/recall", query, "process-recall-corrected")
            assert "绿茶" in updated["rendered_context"]
            invalid = client.get(old_location + "/result", headers=headers())
            assert invalid.status_code == 410 and invalid.json()["code"] == "RESULT_INVALIDATED"
            evidence.update(saved=saved, corrected=corrected, updated_recall=updated)
            cleanup_tasks = []
            for entry in catalog():
                if entry["status"] != "active":
                    continue
                response = client.post(
                    f"/p3/remember/{entry['ref']['memory_id']}/delete",
                    json={"expected_revision": entry["object_revision"], "reason": "test deletion"},
                    headers=headers("delete-" + entry["ref"]["memory_id"]),
                )
                assert response.status_code == 200 and response.json()["blocked"], response.text
                cleanup_tasks.extend(response.json()["task_ids"])
            empty, _ = command("/p3/recall", query, "process-recall-deleted")
            assert empty["rendered_context"] == ""
            for task_id in cleanup_tasks:

                def cleaned(task_id=task_id):
                    response = client.get(f"/p3/tasks/{task_id}", headers=headers())
                    assert response.status_code == 200, response.text
                    task = response.json()
                    assert task["state"] not in {"failed", "attention_required"}, task
                    return task["state"] == "succeeded"

                wait_for(cleaned)
            second.terminate()
            second.wait(timeout=30)
            start("after-delete")
            assert original_result(location) == saved
            deleted_recall, _ = command("/p3/recall", query, "process-recall-deleted-restart")
            assert deleted_recall["rendered_context"] == ""
            assert not list(config.data_dir.rglob("*.db"))
            evidence.update(deleted_recall=deleted_recall, independent_processes=len(children))
            (tmp_path / "business-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8"
            )
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=10)
            for log in logs:
                log.close()
