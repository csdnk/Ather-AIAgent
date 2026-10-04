"""P3 must refuse new caller intents without concrete HTTP request identity."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_run_registry import checkpoint, request
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from azure_component_service import Service

pytestmark = pytest.mark.integration


def test_new_unbound_intent_is_rejected_without_changing_the_run(configuration):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    operation = {
        "method": "POST",
        "path": "/p3/remember/{memory_id}/delete",
        "operation_id": "original",
        "request_hash": "a" * 64,
        "phase": "prepared",
    }
    with TestClient(Service(configuration).app()) as client:
        row = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        proposed = {**row["snapshot"], "state": "running", "operations": [operation]}
        response = client.put(path, json=checkpoint(row, snapshot=proposed), headers=headers())
        assert response.status_code == 409, response.text
        assert client.get(path, headers=headers()).json() == row


def bound(target="/p3/documents/doc?version=1", content_type="text/plain"):
    return ClientOperation.prepare(
        method="PUT",
        path="/p3/documents/{document_id}",
        operation_id="original",
        request_hash="a" * 64,
        target=target,
        content_type=content_type,
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    "change", ["document", "version", "content_type", "drop_binding", "corrupt_digest"]
)
def test_checkpoint_keeps_the_original_concrete_request_binding(configuration, change):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    with TestClient(Service(configuration).app()) as client:
        row = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        original = bound()
        prepared = client.put(
            path,
            json=checkpoint(
                row, snapshot={**row["snapshot"], "state": "running", "operations": [original]}
            ),
            headers=headers(),
        )
        assert prepared.status_code == 200, prepared.text
        row = prepared.json()
        altered = bound(
            target="/p3/documents/other?version=1"
            if change == "document"
            else "/p3/documents/doc?version=2"
            if change == "version"
            else "/p3/documents/doc?version=1",
            content_type="application/json" if change == "content_type" else "text/plain",
        )
        if change == "drop_binding":
            altered.pop("binding")
        if change == "corrupt_digest":
            altered["binding"]["digest"] = "b" * 64
        altered.update(phase="observed", status_code=200, job_id="original-job")
        response = client.put(
            path,
            json=checkpoint(row, snapshot={**row["snapshot"], "operations": [altered]}),
            headers=headers(),
        )
        assert response.status_code == (422 if change == "corrupt_digest" else 409), response.text
        assert client.get(path, headers=headers()).json() == row


@pytest.mark.parametrize("state", ["running", "passed", "failed", "blocked", "unconfirmed"])
def test_legacy_unbound_record_remains_readable_without_inventing_a_binding(configuration, state):
    service = Service(configuration)
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    with TestClient(service.app()) as client:
        row = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        legacy = {key: value for key, value in bound().items() if key != "binding"}
        if state in {"passed", "failed", "blocked"}:
            legacy.update(phase="observed", status_code=200, job_id="original-job")
        ctx = service.runtime.foundation.identity.context("alice")
        row = {
            **row,
            "revision": 2,
            "snapshot": {**row["snapshot"], "state": state, "operations": [legacy]},
        }
        with service.runtime.foundation.uow.transaction() as tx:
            tx.put_if_revision(ClientRuns.ref(ctx, UUID(run_id)), row, 1)
        assert client.get(path, headers=headers()).json() == row
        foreign = client.put(
            path, json=checkpoint(row, state, owner_id="other-owner"), headers=headers()
        )
        assert foreign.status_code == 409, foreign.text
        repeated = client.put(
            path, json=checkpoint(row, state, expected_revision=1), headers=headers()
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == row
        another = str(uuid4())
        assert (
            client.post(
                "/p3/client-runs/" + another, json=request(another), headers=headers()
            ).status_code
            == 409
        )
        if state == "running":
            observed = {**legacy, "phase": "observed", "status_code": 200}
            for operation in (observed, bound()):
                response = client.put(
                    path,
                    json=checkpoint(row, snapshot={**row["snapshot"], "operations": [operation]}),
                    headers=headers(),
                )
                assert response.status_code == 409, response.text
            response = client.put(path, json=checkpoint(row, "unconfirmed"), headers=headers())
            assert response.status_code == 200, response.text
            assert response.json()["snapshot"]["operations"] == [legacy]
    with TestClient(Service(configuration).app()) as client:
        retained = client.get(path, headers=headers()).json()
        assert retained["snapshot"]["operations"] == [legacy]
