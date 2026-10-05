"""Recover original Temporal job identity from an authenticated operation ID."""

from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import command, eventually, headers
from tests.integration.test_current_p2_http import configuration as configuration

from azure_component_service import Service

pytestmark = pytest.mark.integration


def body(name="source"):
    return {
        "source": {
            "kind": "conversation",
            "external_id": name,
            "external_version": "1",
            "occurred_at": "2026-10-03T00:00:00.000Z",
        },
        "selection": {"session_id": "original-session", "task_id": "original-task"},
        "content": {"kind": "text", "text": "I prefer unsweetened coffee."},
    }


@pytest.mark.parametrize(
    "kind", ["remember.save", "recall.execute", "remember.document", "remember.correct"]
)
def test_original_job_lookup_survives_restart_without_reposting(configuration, kind):
    service = Service(configuration)
    operation = "original-request"
    lookup = "/p3/operation-requests/" + operation
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        if kind == "remember.save":
            result, location = command(client, "/p3/remember", body(), operation)
        elif kind == "recall.execute":
            result, location = command(
                client,
                "/p3/recall",
                {
                    "query": "coffee",
                    "selection": {"session_id": "original-session", "task_id": "original-task"},
                    "sources": "working",
                    "token_budget": 1000,
                },
                operation,
            )
        elif kind == "remember.document":
            response = client.put(
                "/p3/documents/original-document",
                params={"version": "1"},
                content=b"Original document body.",
                headers={**headers(operation), "Content-Type": "text/plain"},
            )
            location = response.headers["Location"]
            result = eventually(
                lambda: (
                    r.json()
                    if (r := client.get(location + "/result", headers=headers())).status_code == 200
                    else None
                )
            )
        else:
            saved, _ = command(client, "/p3/remember", body("seed"), "seed-save")
            memory = saved["memories"][0]
            result, location = command(
                client,
                f"/p3/remember/{memory['memory_id']}/correct",
                {
                    "expected_version": memory["version"],
                    "content": "I now prefer green tea.",
                    "reason": "user correction",
                    "source": body("correction")["source"],
                },
                operation,
            )
        original_job = location.rsplit("/", 1)[-1]
        found = client.get(lookup, params={"kind": kind}, headers=headers())
        assert found.status_code == 200, found.text
        value = found.json()
        assert value["state"] == "found" and value["job_id"] == original_job
        assert value["operation_id"] == operation and value["kind"] == kind
        assert value["task_state"] == "succeeded"
        assert value["workflow_id"].endswith("/" + kind + "/" + original_job)
        assert len(value["input_hash"]) == 64
        assert client.get(lookup, params={"kind": kind}).status_code == 401
        foreign = client.get(lookup, params={"kind": kind}, headers=headers(user="eve"))
        assert foreign.status_code == 200 and foreign.json()["state"] == "unconfirmed"
        assert foreign.json()["job_id"] is None
        with service.runtime.foundation.uow.transaction() as tx:
            count_before = len(tx.rows("tasks"))
        assert client.get(lookup, params={"kind": kind}, headers=headers()).json() == value
        with service.runtime.foundation.uow.transaction() as tx:
            assert len(tx.rows("tasks")) == count_before

    with TestClient(Service(configuration).app()) as client:
        recovered = client.get(lookup, params={"kind": kind}, headers=headers())
        assert recovered.status_code == 200 and recovered.json() == value
        assert client.get(location + "/result", headers=headers()).json() == result


def test_lookup_does_not_treat_missing_admission_as_proof_of_no_effect(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        ctx = service.runtime.foundation.identity.context("alice", operation_id="before-admission")
        service.execution.inputs.persist(ctx, ctx.operation_id, b"durable input only", "text/plain")
        with service.runtime.foundation.uow.transaction() as tx:
            before = len(tx.rows("tasks"))
        for operation in ("before-admission", "never-observed"):
            response = client.get(
                "/p3/operation-requests/" + operation,
                params={"kind": "remember.save"},
                headers=headers(),
            )
            assert response.status_code == 200, response.text
            assert response.json()["state"] == "unconfirmed"
            assert response.json()["job_id"] is None
        with service.runtime.foundation.uow.transaction() as tx:
            assert len(tx.rows("tasks")) == before


def test_lookup_rejects_revoked_epoch_and_changed_temporal_binding(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        _, location = command(client, "/p3/remember", body(), "bound-request")
        lookup = "/p3/operation-requests/bound-request?kind=remember.save"
        assert client.get(lookup, headers=headers()).status_code == 200
        job = location.rsplit("/", 1)[-1]
        with service.runtime.foundation.uow.transaction() as tx:
            original = tx.read("temporal_bindings", job)
            tx.write("temporal_bindings", job, {**original, "workflow_id": "another-workflow"})
        changed = client.get(lookup, headers=headers())
        assert changed.status_code == 409, changed.text
        with service.runtime.foundation.uow.transaction() as tx:
            tx.write("temporal_bindings", job, original)
        identity = service.runtime.foundation.identity
        principal = identity.authenticate("alice")
        identity.provision(
            [(sha256(b"alice").hexdigest(), principal.model_copy(update={"auth_epoch": 2}))]
        )
        assert client.get(lookup, headers=headers()).status_code == 403


def test_lookup_preserves_failed_job_instead_of_hiding_it_as_missing(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        response = client.post(
            "/p3/recall",
            json={"query": "coffee", "selection": {}, "sources": "working", "token_budget": 1000},
            headers=headers("failed-working-query"),
        )
        location = response.headers["Location"]

        def failed():
            value = client.get(location, headers=headers()).json()
            return value if value["state"] in {"failed", "attention_required"} else None

        terminal = eventually(failed)
        assert terminal["error_code"] == "INVALID_ARGUMENT"
        found = client.get(
            "/p3/operation-requests/failed-working-query",
            params={"kind": "recall.execute"},
            headers=headers(),
        )
        assert found.status_code == 200, found.text
        assert found.json()["state"] == "found"
        assert found.json()["job_id"] == terminal["task_id"]
        assert found.json()["task_state"] == terminal["state"]
