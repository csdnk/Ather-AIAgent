"""Journaled HTTP effects must pass the current caller fence in their transaction."""

import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_inputs import owner_headers, prepare
from tests.integration.test_client_run_registry import checkpoint, request
from tests.integration.test_current_p2_http import command, eventually, headers
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_operation_lookup import body

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from azure_component_service import Service

pytestmark = pytest.mark.integration


def admission_headers(record, operation_id="original-input"):
    return {
        **owner_headers(record),
        **headers(operation_id),
        "X-P3-Run-ID": record["run_id"],
        "Content-Type": "application/json",
    }


def original_request(client, *, save=True):
    payload = json.dumps(body(), ensure_ascii=False).encode()
    record, intent, path = prepare(client, payload)
    if save:
        result = client.put(path, content=payload, headers=owner_headers(record))
        assert result.status_code == 200, result.text
    return record, intent, payload


def register_operation(client, payload, route, target, *, method="POST"):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    registered = client.post(path, json=request(run_id), headers=headers())
    assert registered.status_code == 201, registered.text
    record = registered.json()["record"]
    intent = ClientOperation.prepare(
        method=method,
        path=route,
        target=target,
        operation_id="original-input",
        request_hash=sha256(payload).hexdigest(),
        content_type="application/json",
    )
    response = client.put(
        path,
        headers=headers(),
        json=checkpoint(
            record,
            snapshot={
                **record["snapshot"],
                "state": "running",
                "operations": [intent.model_dump(mode="json")],
            },
        ),
    )
    assert response.status_code == 200, response.text
    record = response.json()
    stored = client.put(
        path + "/inputs/original-input", content=payload, headers=owner_headers(record)
    )
    assert stored.status_code == 200, stored.text
    return record


@pytest.mark.parametrize("change", ["missing", "owner", "revision", "run", "body"])
def test_journaled_command_cannot_bypass_original_owner_and_bytes(configuration, change):
    with TestClient(Service(configuration).app()) as client:
        record, _, payload = original_request(client)
        values = admission_headers(record)
        if change == "missing":
            values = {**headers("original-input"), "Content-Type": "application/json"}
        elif change == "owner":
            values["X-P3-Run-Owner"] = "old-executor"
        elif change == "revision":
            values["X-P3-Run-Revision"] = "1"
        elif change == "run":
            values["X-P3-Run-ID"] = "11111111-1111-4111-8111-111111111111"
        else:
            payload = payload.replace(b"coffee", b"tea")
        response = client.post("/p3/remember", content=payload, headers=values)
        assert response.status_code == 409, response.text
        found = client.get(
            "/p3/operation-requests/original-input?kind=remember.save", headers=headers()
        ).json()
        assert found["state"] == "unconfirmed" and found["job_id"] is None


def test_journaled_command_requires_confirmed_original_input(configuration):
    with TestClient(Service(configuration).app()) as client:
        record, _, payload = original_request(client, save=False)
        response = client.post("/p3/remember", content=payload, headers=admission_headers(record))
        assert response.status_code == 400 and response.json()["code"] == "COMMIT_UNCONFIRMED"
        assert (
            client.get(
                "/p3/operation-requests/original-input?kind=remember.save", headers=headers()
            ).json()["state"]
            == "unconfirmed"
        )


def test_original_task_survives_owner_change_and_remains_queryable(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, payload = original_request(client)
        response = client.post("/p3/remember", content=payload, headers=admission_headers(record))
        assert response.status_code == 200 or response.json().get("code") == "REQUEST_IN_PROGRESS"
        job_id = response.headers["X-P3-Job-ID"]
        foundation = service.runtime.foundation
        ref = ClientRuns.ref(foundation.identity.context("alice"), UUID(record["run_id"]))
        with foundation.uow.transaction() as tx:
            current = tx.get(ref)
            tx.put_if_revision(
                ref,
                {**current, "owner_id": "replacement", "revision": current["revision"] + 1},
                current["revision"],
            )
        refused = client.post("/p3/remember", content=payload, headers=admission_headers(record))
        assert refused.status_code == 409, refused.text
        result = eventually(
            lambda: (
                value.json()
                if (
                    value := client.get(f"/p3/operations/{job_id}/result", headers=headers())
                ).status_code
                == 200
                else None
            )
        )
        assert result["operation_id"] == "original-input" and result["saved"]
        original = client.get(
            "/p3/operation-requests/original-input?kind=remember.save", headers=headers()
        ).json()
        assert original["job_id"] == job_id
        assert original["http_request"]["client_run"] == {
            "run_id": record["run_id"],
            "owner_id": "worker-a",
            "revision": record["revision"],
        }
        assert original["http_request"]["body_hash"] == sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "action",
    [
        "lifecycle",
        "retention",
        "delete",
        "reindex",
        "reprocess",
        "reflection",
        "consolidate",
        "distill",
        "source-delete",
        "source-revoke",
    ],
)
def test_each_journaled_mutation_requires_owner_before_business_change(configuration, action):
    with TestClient(Service(configuration).app()) as client:
        saved, _ = command(client, "/p3/remember", body(), "seed")
        mid = saved["memories"][0]["memory_id"]
        item = client.get("/p3/remember/" + mid, headers=headers()).json()
        route = "/p3/remember/{memory_id}/" + action
        path = f"/p3/remember/{mid}/{action}"
        value = None
        if action == "lifecycle":
            value = {"expected_version": 1, "target": "archived", "reason": "test"}
        elif action == "retention":
            value = {
                "expected_version": 1,
                "expected_object_revision": item["object_revision"],
                "enabled": False,
                "reason": "test",
            }
        elif action == "delete":
            value = {"expected_revision": item["object_revision"], "reason": "test"}
        elif action in {"reflection", "consolidate"}:
            route = path = "/p3/remember/" + action
            value = (
                body()["selection"]
                if action == "consolidate"
                else {"selection": body()["selection"], "enabled": False, "reason": "test"}
            )
        elif action == "distill":
            route = path = "/p3/remember/distill"
            value = eventually(
                lambda: [
                    entry["ref"]
                    for entry in client.get("/p3/memories", headers=headers()).json()["items"]
                    if entry["kind"] == "episodic"
                ]
            )
        elif action.startswith("source-"):
            route = "/p3/sources/{source_id}/" + action[7:]
            path = f"/p3/sources/{saved['source']['source_id']}/{action[7:]}"
            value = {"expected_revision": 1, "reason": "test"}
        payload = json.dumps(value).encode() if value is not None else b""
        record = register_operation(client, payload, route, path)
        response = client.post(
            path,
            content=payload,
            headers={**admission_headers(record), "X-P3-Run-Owner": "old-executor"},
        )
        assert response.status_code == 409, response.text
        kind = "source." + action[7:] if action.startswith("source-") else "remember." + action
        lookup = client.get(
            "/p3/mutation-receipts/original-input", params={"kind": kind}, headers=headers()
        ).json()
        assert lookup["state"] == "unconfirmed"
        # The original valid request is still usable: rejection did not change its target.
        accepted = client.post(path, content=payload, headers=admission_headers(record))
        assert accepted.status_code == 200, accepted.text
        receipt = client.get(
            "/p3/mutation-receipts/original-input", params={"kind": kind}, headers=headers()
        ).json()["receipt"]
        assert receipt["http_request"]["client_run"]["owner_id"] == record["owner_id"]


def test_checkpoint_and_operation_index_commit_atomically(configuration, monkeypatch):
    service = Service(configuration)
    original = PostgresTransaction.put_if_revision

    def unavailable(self, ref, value, expected_revision):
        if ref.object_type == "client_operation":
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "injected index failure")
        return original(self, ref, value, expected_revision)

    with TestClient(service.app()) as client:
        run_id = str(uuid4())
        path = "/p3/client-runs/" + run_id
        record = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        intent = ClientOperation.prepare(
            method="POST",
            path="/p3/remember",
            target="/p3/remember",
            operation_id="original-input",
            request_hash="a" * 64,
            content_type="application/json",
        )
        monkeypatch.setattr(PostgresTransaction, "put_if_revision", unavailable)
        response = client.put(
            path,
            headers=headers(),
            json=checkpoint(
                record,
                snapshot={
                    **record["snapshot"],
                    "state": "running",
                    "operations": [intent.model_dump(mode="json")],
                },
            ),
        )
        assert response.status_code == 503, response.text
        assert client.get(path, headers=headers()).json() == record


@pytest.mark.parametrize("document", [False, True])
def test_rejected_caller_cannot_poison_the_original_command_input(configuration, document):
    with TestClient(Service(configuration).app()) as client:
        if document:
            payload = b"original document coffee"
            route, path, method = (
                "/p3/documents/{document_id}",
                "/p3/documents/original?version=1",
                "PUT",
            )
            record = register_operation(client, payload, route, path, method=method)
        else:
            record, _, payload = original_request(client)
            method, path = "POST", "/p3/remember"
        refused = client.request(
            method,
            path,
            content=payload.replace(b"coffee", b"tea"),
            headers={**admission_headers(record), "X-P3-Run-Owner": "old-executor"},
        )
        assert refused.status_code == 409, refused.text
        accepted = client.request(method, path, content=payload, headers=admission_headers(record))
        assert "X-P3-Job-ID" in accepted.headers, accepted.text


@pytest.mark.parametrize("change", ["absent-owner", "duplicate-owner", "revision", "run"])
def test_malformed_executor_headers_are_rejected_before_admission(configuration, change):
    with TestClient(Service(configuration).app()) as client:
        record, _, payload = original_request(client)
        values = admission_headers(record)
        if change == "absent-owner":
            del values["X-P3-Run-Owner"]
        elif change == "revision":
            values["X-P3-Run-Revision"] = "2.0"
        elif change == "run":
            values["X-P3-Run-ID"] = "not-a-uuid"
        pairs = list(values.items())
        if change == "duplicate-owner":
            pairs.append(("X-P3-Run-Owner", "worker-a"))
        result = client.post("/p3/remember", content=payload, headers=pairs)
        assert result.status_code == 400 and result.json()["code"] == "INVALID_ARGUMENT"
        assert "X-P3-Job-ID" not in result.headers


@pytest.mark.parametrize("kind", ["recall.execute", "remember.correct", "remember.document"])
def test_other_temporal_commands_enforce_the_same_original_caller(configuration, kind):
    with TestClient(Service(configuration).app()) as client:
        method = "POST"
        if kind == "recall.execute":
            path = route = "/p3/recall"
            payload = json.dumps(
                {"query": "coffee", "selection": body()["selection"], "sources": "working"}
            ).encode()
        elif kind == "remember.correct":
            saved, _ = command(client, "/p3/remember", body("seed"), "seed")
            path = f"/p3/remember/{saved['memories'][0]['memory_id']}/correct"
            route = "/p3/remember/{memory_id}/correct"
            payload = json.dumps(
                {
                    "expected_version": 1,
                    "content": "tea",
                    "reason": "test",
                    "source": body("correction")["source"],
                }
            ).encode()
        else:
            method = "PUT"
            path, route = "/p3/documents/original?version=1", "/p3/documents/{document_id}"
            payload = b"original document"
        record = register_operation(client, payload, route, path, method=method)
        refused = client.request(
            method,
            path,
            content=payload,
            headers={**admission_headers(record), "X-P3-Run-Owner": "old-executor"},
        )
        assert refused.status_code == 409 and "X-P3-Job-ID" not in refused.headers
        assert (
            client.get(
                "/p3/operation-requests/original-input", params={"kind": kind}, headers=headers()
            ).json()["state"]
            == "unconfirmed"
        )
        accepted = client.request(method, path, content=payload, headers=admission_headers(record))
        assert "X-P3-Job-ID" in accepted.headers, accepted.text


@pytest.mark.parametrize("change", ["document", "version", "content_type"])
def test_document_admission_keeps_original_target_and_content_type(configuration, change):
    with TestClient(Service(configuration).app()) as client:
        path, payload = "/p3/documents/original?version=1", b"original document"
        record = register_operation(
            client, payload, "/p3/documents/{document_id}", path, method="PUT"
        )
        values = admission_headers(record)
        if change == "content_type":
            values["Content-Type"] = "text/plain"
        else:
            path = (
                "/p3/documents/other?version=1"
                if change == "document"
                else "/p3/documents/original?version=2"
            )
        result = client.put(path, content=payload, headers=values)
        assert result.status_code == 409 and result.json()["code"] == "IDEMPOTENCY_CONFLICT"
        assert "X-P3-Job-ID" not in result.headers


def test_final_transaction_rechecks_owner_after_p2_input_io(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, payload = original_request(client)
        foundation = service.runtime.foundation
        ref = ClientRuns.ref(foundation.identity.context("alice"), UUID(record["run_id"]))
        original = service.execution.inputs.objects.get_object_sync
        changed = False

        def replace_owner(key):
            nonlocal changed
            result = original(key)
            if key.startswith("runtime/inputs/") and not changed:
                with foundation.uow.transaction() as tx:
                    current = tx.get(ref)
                    tx.put_if_revision(
                        ref,
                        {**current, "owner_id": "replacement", "revision": current["revision"] + 1},
                        current["revision"],
                    )
                changed = True
            return result

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", replace_owner)
        response = client.post("/p3/remember", content=payload, headers=admission_headers(record))
        assert changed and response.status_code == 409, response.text
        assert (
            client.get(
                "/p3/operation-requests/original-input?kind=remember.save", headers=headers()
            ).json()["state"]
            == "unconfirmed"
        )
        # An original input staged before the race stays bound and is not regenerated.
        current = client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json()
        accepted = client.post("/p3/remember", content=payload, headers=admission_headers(current))
        assert "X-P3-Job-ID" in accepted.headers, accepted.text


def test_observed_operation_cannot_be_posted_or_rebound_to_a_later_run(configuration):
    with TestClient(Service(configuration).app()) as client:
        record, intent, payload = original_request(client)
        path = "/p3/client-runs/" + record["run_id"]
        observed = {**intent.model_dump(mode="json"), "phase": "observed", "status_code": 400}
        current = client.put(
            path,
            headers=headers(),
            json=checkpoint(record, snapshot={**record["snapshot"], "operations": [observed]}),
        ).json()
        refused = client.post("/p3/remember", content=payload, headers=admission_headers(current))
        assert refused.status_code == 409 and "X-P3-Job-ID" not in refused.headers
        finished = client.put(path, headers=headers(), json=checkpoint(current, "failed"))
        assert finished.status_code == 200, finished.text
        later_id = str(uuid4())
        later_path = "/p3/client-runs/" + later_id
        later = client.post(later_path, json=request(later_id), headers=headers()).json()["record"]
        rebound = client.put(
            later_path,
            headers=headers(),
            json=checkpoint(
                later,
                snapshot={
                    **later["snapshot"],
                    "state": "running",
                    "operations": [intent.model_dump(mode="json")],
                },
            ),
        )
        assert rebound.status_code == 409 and rebound.json()["code"] == "IDEMPOTENCY_CONFLICT"
        assert client.get(later_path, headers=headers()).json() == later


def test_admission_index_and_original_executor_evidence_survive_service_restart(configuration):
    with TestClient(Service(configuration).app()) as client:
        record, _, payload = original_request(client)
    with TestClient(Service(configuration).app()) as client:
        refused = client.post(
            "/p3/remember",
            content=payload,
            headers={**headers("original-input"), "Content-Type": "application/json"},
        )
        assert refused.status_code == 409, refused.text
        accepted = client.post("/p3/remember", content=payload, headers=admission_headers(record))
        job_id = accepted.headers["X-P3-Job-ID"]
        eventually(
            lambda: (
                client.get(f"/p3/operations/{job_id}/result", headers=headers()).status_code == 200
            )
        )
    with TestClient(Service(configuration).app()) as client:
        evidence = client.get(
            "/p3/operation-requests/original-input?kind=remember.save", headers=headers()
        ).json()
        assert evidence["job_id"] == job_id
        assert evidence["http_request"]["client_run"]["run_id"] == record["run_id"]


def test_running_temporal_task_completes_after_caller_owner_changes(configuration, monkeypatch):
    service = Service(configuration)
    entered, release = Event(), Event()
    stage = service.execution.registry.get("remember.save", "prepare")

    async def held(step):
        entered.set()
        assert await asyncio.to_thread(release.wait, 15), "test did not release the original task"
        return await stage.execute(step)

    monkeypatch.setitem(
        service.execution.registry.routes["remember.save"], "prepare", replace(stage, execute=held)
    )
    with TestClient(service.app()) as client:
        record, _, payload = original_request(client)
        try:
            response = client.post(
                "/p3/remember", content=payload, headers=admission_headers(record)
            )
            assert response.json()["code"] == "REQUEST_IN_PROGRESS", response.text
            job_id = response.headers["X-P3-Job-ID"]
            assert entered.wait(10), "original Temporal task did not reach its execution stage"
            task = client.get(f"/p3/tasks/{job_id}", headers=headers()).json()
            assert task["state"] == "running" and task["result_ref"] is None
            original_execution = task["execution"]
            foundation = service.runtime.foundation
            ref = ClientRuns.ref(foundation.identity.context("alice"), UUID(record["run_id"]))
            with foundation.uow.transaction() as tx:
                prior = tx.get(ref)
                tx.put_if_revision(
                    ref,
                    {**prior, "owner_id": "replacement", "revision": prior["revision"] + 1},
                    prior["revision"],
                )
            rejected = client.post(
                "/p3/remember", content=payload, headers=admission_headers(record)
            )
            assert rejected.status_code == 409 and "X-P3-Job-ID" not in rejected.headers
        finally:
            release.set()
        result = eventually(
            lambda: (
                response.json()
                if (
                    response := client.get(f"/p3/operations/{job_id}/result", headers=headers())
                ).status_code
                == 200
                else None
            )
        )
        assert result["saved"] and result["operation_id"] == "original-input"
        finished = client.get(f"/p3/tasks/{job_id}", headers=headers()).json()
        assert finished["state"] == "succeeded"
        assert finished["execution"]["workflow_id"] == original_execution["workflow_id"]
        assert finished["execution"]["run_id"] == original_execution["run_id"]
        assert finished["input_hash"] == task["input_hash"]
