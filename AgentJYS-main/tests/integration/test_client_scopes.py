"""Managed run namespaces must fence writes even under a new operation ID."""

import json
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_admission import admission_headers
from tests.integration.test_client_inputs import owner_headers
from tests.integration.test_client_run_registry import checkpoint, request
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually, headers
from tests.integration.test_operation_lookup import body

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.client_scopes import namespace_ref
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from aether_agent_memory.runtime.temporal.ingress import InputStore
from azure_component_service import Service

pytestmark = pytest.mark.integration


def managed_run(client):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    spec = {**request(run_id), "scope_policy": "p4_task_v1"}
    response = client.post(path, json=spec, headers=headers())
    assert response.status_code == 201, response.text
    record = response.json()["record"]
    assert record["scope_policy"] == "p4_task_v1"
    return record


def scope_of(record):
    return {"task_id": record["scope_id"], "session_id": record["scope_id"] + "_session"}


def remember_body(record):
    return {**body(), "selection": scope_of(record)}


def prepare_operation(
    client, record, payload, route, path=None, *, operation="managed-op", method="POST"
):
    path = path or route
    intent = ClientOperation.prepare(
        method=method,
        path=route,
        target=path,
        operation_id=operation,
        request_hash=sha256(payload).hexdigest(),
        content_type="application/json",
    )
    snapshot = {
        **record["snapshot"],
        "state": "running",
        "operations": [*record["snapshot"].get("operations", []), intent.model_dump(mode="json")],
    }
    response = client.put(
        "/p3/client-runs/" + record["run_id"],
        headers=headers(),
        json=checkpoint(record, snapshot=snapshot),
    )
    assert response.status_code == 200, response.text
    record = response.json()
    stored = client.put(
        f"/p3/client-runs/{record['run_id']}/inputs/{operation}",
        content=payload,
        headers=owner_headers(record),
    )
    assert stored.status_code == 200, stored.text
    return record


def test_run_namespace_and_registration_are_persistent(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = managed_run(client)
    with TestClient(Service(configuration).app()) as client:
        found = client.get("/p3/client-runs/" + record["run_id"], headers=headers())
        assert found.json() == record
        response = client.post(
            "/p3/remember", json=remember_body(record), headers=headers("new-op")
        )
        assert response.status_code == 409 and "X-P3-Job-ID" not in response.headers


@pytest.mark.parametrize("target", ["same", "session-only", "wrong-session"])
def test_new_operation_id_cannot_write_into_managed_scope(configuration, target):
    with TestClient(Service(configuration).app()) as client:
        record = managed_run(client)
        value = remember_body(record)
        if target == "session-only":
            value["selection"].pop("task_id")
        elif target == "wrong-session":
            value["selection"]["session_id"] = "outside-session"
        response = client.post("/p3/remember", json=value, headers=headers("fresh-op"))
        assert response.status_code == 409 and "X-P3-Job-ID" not in response.headers


def test_managed_claim_cannot_write_outside_its_scope(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = managed_run(client)
        payload = json.dumps(body()).encode()
        record = prepare_operation(client, record, payload, "/p3/remember")
        result = client.post(
            "/p3/remember", content=payload, headers=admission_headers(record, "managed-op")
        )
        assert result.status_code == 403 and "X-P3-Job-ID" not in result.headers


def test_original_managed_write_works_and_ordinary_scopes_still_work(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = managed_run(client)
        payload = json.dumps(remember_body(record)).encode()
        record = prepare_operation(client, record, payload, "/p3/remember")
        result = client.post(
            "/p3/remember", content=payload, headers=admission_headers(record, "managed-op")
        )
        assert "X-P3-Job-ID" in result.headers, result.text
        job_id = result.headers["X-P3-Job-ID"]
        saved = eventually(
            lambda: (
                reply.json()
                if (
                    reply := client.get(f"/p3/operations/{job_id}/result", headers=headers())
                ).status_code
                == 200
                else None
            )
        )
        assert saved["memories"][0]["scope"]["task_id"] == record["scope_id"]
        ordinary = client.post("/p3/remember", json=body("ordinary"), headers=headers("ordinary"))
        assert "X-P3-Job-ID" in ordinary.headers, ordinary.text
        read = client.get("/p3/remember/" + saved["memories"][0]["memory_id"], headers=headers())
        assert read.status_code == 200


@pytest.mark.parametrize("known", [False, True])
def test_unregistered_operation_cannot_preempt_managed_document_namespace(configuration, known):
    with TestClient(Service(configuration).app()) as client:
        scope_id = managed_run(client)["scope_id"] if known else "p4r_" + "a" * 32
        result = client.put(
            f"/p3/documents/{scope_id}_rules?version=1",
            content=b"original rules",
            headers={**headers("unregistered-upload"), "Content-Type": "text/plain"},
        )
        assert result.status_code == 409 and "X-P3-Job-ID" not in result.headers


def seed_managed(client):
    record = managed_run(client)
    payload = json.dumps(remember_body(record)).encode()
    record = prepare_operation(client, record, payload, "/p3/remember", operation="seed")
    response = client.post(
        "/p3/remember", content=payload, headers=admission_headers(record, "seed")
    )
    job_id = response.headers["X-P3-Job-ID"]
    saved = eventually(
        lambda: (
            reply.json()
            if (
                reply := client.get(f"/p3/operations/{job_id}/result", headers=headers())
            ).status_code
            == 200
            else None
        )
    )
    return record, saved


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
def test_every_mutation_rejects_unjournaled_managed_target(configuration, action):
    with TestClient(Service(configuration).app()) as client:
        record, saved = seed_managed(client)
        mid = saved["memories"][0]["memory_id"]
        item = client.get("/p3/remember/" + mid, headers=headers()).json()
        route, path, value = (
            "/p3/remember/{memory_id}/" + action,
            f"/p3/remember/{mid}/{action}",
            None,
        )
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
                scope_of(record)
                if action == "consolidate"
                else {"selection": scope_of(record), "enabled": False, "reason": "test"}
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
        refused = client.post(
            path,
            content=payload,
            headers={**headers("unjournaled"), "Content-Type": "application/json"},
        )
        assert refused.status_code == 409, refused.text
        kind = "source." + action[7:] if action.startswith("source-") else "remember." + action
        assert (
            client.get(
                "/p3/mutation-receipts/unjournaled", params={"kind": kind}, headers=headers()
            ).json()["state"]
            == "unconfirmed"
        )
        record = prepare_operation(client, record, payload, route, path, operation="valid-mutation")
        accepted = client.post(
            path, content=payload, headers=admission_headers(record, "valid-mutation")
        )
        assert accepted.status_code == 200, accepted.text


def test_correction_checks_actual_metadata_without_staging_rejected_input(
    configuration, monkeypatch
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, saved = seed_managed(client)
        mid = saved["memories"][0]["memory_id"]

        def forbidden(*args, **kwargs):
            raise AssertionError("rejected correction must not read body or stage immutable input")

        monkeypatch.setattr(service.runtime.remember, "decode", forbidden)
        monkeypatch.setattr(service.execution.inputs, "persist", forbidden)
        value = {
            "expected_version": 1,
            "content": "tea",
            "reason": "correction",
            "source": body("correction")["source"],
        }
        result = client.post(
            f"/p3/remember/{mid}/correct", json=value, headers=headers("unregistered-correction")
        )
        assert result.status_code == 409, result.text


def test_namespace_reservation_is_atomic_with_registration(configuration, monkeypatch):
    original = PostgresTransaction.put_if_revision

    def fail(self, ref, value, revision):
        if ref.object_type == "client_run_index":
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "injected index write failure")
        return original(self, ref, value, revision)

    service = Service(configuration)
    with TestClient(service.app()) as client:
        run_id = str(uuid4())
        spec = {**request(run_id), "scope_policy": "p4_task_v1"}
        with monkeypatch.context() as patch:
            patch.setattr(PostgresTransaction, "put_if_revision", fail)
            response = client.post("/p3/client-runs/" + run_id, json=spec, headers=headers())
            assert response.status_code == 503, response.text
        assert client.get("/p3/client-runs/" + run_id, headers=headers()).status_code == 404
        retried = client.post("/p3/client-runs/" + run_id, json=spec, headers=headers())
        assert retried.status_code == 201, retried.text
        again = client.post("/p3/client-runs/" + run_id, json=spec, headers=headers())
        assert again.status_code == 200 and again.json()["record"] == retried.json()["record"]


def test_final_admission_rechecks_namespace_after_p2_input_read(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = managed_run(client)
        payload = json.dumps(remember_body(record)).encode()
        record = prepare_operation(client, record, payload, "/p3/remember")
        original = InputStore.read

        def change(self, ctx, ref):
            data = original(self, ctx, ref)
            with self.uow.transaction() as tx:
                reserved = namespace_ref(ctx.principal.home_scope, record["scope_id"])
                value = tx.get(reserved)
                other = ClientRuns.ref(ctx, uuid4())
                tx.put_if_revision(
                    reserved,
                    {**value, "run_ref": other.model_dump(mode="json")},
                    tx.revision(reserved),
                )
            return data

        with monkeypatch.context() as patch:
            patch.setattr(InputStore, "read", change)
            result = client.post(
                "/p3/remember", content=payload, headers=admission_headers(record, "managed-op")
            )
        assert result.status_code == 403 and "X-P3-Job-ID" not in result.headers, result.text
        found = client.get(
            "/p3/operation-requests/managed-op?kind=remember.save", headers=headers()
        ).json()
        assert found["state"] == "unconfirmed" and found["job_id"] is None


def test_explicit_managed_recall_is_fenced_but_unscoped_recall_is_available(configuration):
    with TestClient(Service(configuration).app()) as client:
        record, _ = seed_managed(client)
        value = {"query": "coffee", "selection": scope_of(record)}
        refused = client.post("/p3/recall", json=value, headers=headers("unregistered-recall"))
        assert refused.status_code == 409 and "X-P3-Job-ID" not in refused.headers, refused.text
        payload = json.dumps(value).encode()
        record = prepare_operation(client, record, payload, "/p3/recall", operation="valid-recall")
        accepted = client.post(
            "/p3/recall", content=payload, headers=admission_headers(record, "valid-recall")
        )
        assert "X-P3-Job-ID" in accepted.headers, accepted.text
        ordinary = client.post(
            "/p3/recall",
            json={"query": "coffee", "selection": {}},
            headers=headers("ordinary-recall"),
        )
        assert "X-P3-Job-ID" in ordinary.headers, ordinary.text


def test_terminal_namespace_stays_reserved_and_new_run_cannot_target_it(configuration):
    with TestClient(Service(configuration).app()) as client:
        first, saved = seed_managed(client)
        response = client.put(
            "/p3/client-runs/" + first["run_id"],
            headers=headers(),
            json=checkpoint(
                first,
                snapshot={
                    **first["snapshot"],
                    "state": "passed",
                    "operations": [
                        {**op, "phase": "observed", "status_code": 200}
                        for op in first["snapshot"]["operations"]
                    ],
                },
            ),
        )
        assert response.status_code == 200, response.text
        raw = json.dumps(remember_body(first)).encode()
        ordinary = client.post(
            "/p3/remember",
            content=raw,
            headers={**headers("new-id"), "Content-Type": "application/json"},
        )
        assert ordinary.status_code == 409, ordinary.text
        second = managed_run(client)
        second = prepare_operation(client, second, raw, "/p3/remember", operation="other-run")
        refused = client.post(
            "/p3/remember", content=raw, headers=admission_headers(second, "other-run")
        )
        assert refused.status_code == 403 and "X-P3-Job-ID" not in refused.headers, refused.text
        assert (
            client.get(
                "/p3/remember/" + saved["memories"][0]["memory_id"], headers=headers()
            ).status_code
            == 200
        )


def test_unscoped_collection_writes_do_not_change_managed_policies(configuration):
    with TestClient(Service(configuration).app()) as client:
        record, _ = seed_managed(client)
        route = "/p3/remember/reflection"
        params = scope_of(record)
        before = client.get(route, params=params, headers=headers()).json()
        changed = client.post(
            route,
            json={"selection": {}, "enabled": False, "reason": "unrelated"},
            headers=headers("ordinary-policy"),
        )
        assert changed.status_code == 200, changed.text
        assert client.get(route, params=params, headers=headers()).json() == before
        consolidated = client.post(
            "/p3/remember/consolidate", json={}, headers=headers("ordinary-consolidate")
        )
        assert consolidated.status_code == 200, consolidated.text
        assert consolidated.json()["task_ids"] == []


def test_legacy_registration_does_not_acquire_scope_policy(configuration):
    with TestClient(Service(configuration).app()) as client:
        run_id = str(uuid4())
        path = "/p3/client-runs/" + run_id
        spec = request(run_id)
        old = client.post(path, json=spec, headers=headers()).json()["record"]
        assert old["scope_policy"] is None and old["scope_id"].startswith("demo_")
        refused = client.post(path, json={**spec, "scope_policy": "p4_task_v1"}, headers=headers())
        assert refused.status_code == 409, refused.text
        assert client.get(path, headers=headers()).json() == old


def test_managed_registration_rejects_home_bound_to_task(configuration):
    import yaml

    value = yaml.safe_load(configuration.identity_file.read_text(encoding="utf-8"))
    value["identities"][0]["principal"]["home_scope"]["task_id"] = "prebound-task"
    configuration.identity_file.write_text(yaml.safe_dump(value), encoding="utf-8")
    with TestClient(Service(configuration).app()) as client:
        run_id = str(uuid4())
        path = "/p3/client-runs/" + run_id
        refused = client.post(
            path, json={**request(run_id), "scope_policy": "p4_task_v1"}, headers=headers()
        )
        assert refused.status_code == 403, refused.text
        ordinary = client.post(path, json=request(run_id), headers=headers())
        assert ordinary.status_code == 201, ordinary.text
