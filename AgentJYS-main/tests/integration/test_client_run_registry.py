"""Caller run metadata uses P3's configured transaction port, never a P4 file store."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from azure_component_service import Service

pytestmark = pytest.mark.integration


def intent(body_hash="a" * 64):
    return ClientOperation.prepare(
        method="POST",
        path="/p3/remember",
        operation_id="original-op",
        request_hash=body_hash,
        target="/p3/remember",
        content_type="application/json",
    ).model_dump(mode="json")


def request(run_id, scenario="library-basic", owner="worker-a"):
    return {
        "scenario_id": scenario,
        "owner_id": owner,
        "snapshot": {"run_id": run_id, "scenario_id": scenario, "state": "queued", "steps": []},
    }


def checkpoint(record, state="passed", **changes):
    return {
        "owner_id": record["owner_id"],
        "expected_revision": record["revision"],
        "snapshot": {**record["snapshot"], "state": state},
        **changes,
    }


def test_completed_registry_survives_p3_reconstruction_and_keeps_original_binding(configuration):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    body = request(run_id)
    with TestClient(Service(configuration).app()) as client:
        created = client.post(path, json=body, headers=headers())
        assert created.status_code == 201, created.text
        record = created.json()["record"]
        committed = client.put(path, json=checkpoint(record), headers=headers())
        assert committed.status_code == 200, committed.text
        final = committed.json()
    with TestClient(Service(configuration).app()) as client:
        recovered = client.get(path, headers=headers())
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == final
        repeated = client.post(path, json=request(run_id, owner="worker-b"), headers=headers())
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == {"created": False, "record": final}
        assert final["scope_id"] == record["scope_id"]


@pytest.mark.parametrize("same_id", [True, False])
def test_concurrent_admission_creates_one_run_and_retains_active_slot(configuration, same_id):
    first, second = str(uuid4()), str(uuid4())
    ids = [first, first if same_id else second]
    with TestClient(Service(configuration).app()) as client, ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(
                lambda value: client.post(
                    "/p3/client-runs/" + value, json=request(value), headers=headers()
                ),
                ids,
            )
        )
        assert sorted(r.status_code for r in responses) == ([200, 201] if same_id else [201, 409])
        created = next(r.json()["record"] for r in responses if r.status_code == 201)
        unconfirmed = client.put(
            "/p3/client-runs/" + created["run_id"],
            json=checkpoint(created, "unconfirmed"),
            headers=headers(),
        )
        assert unconfirmed.status_code == 200
        third = str(uuid4())
        assert (
            client.post(
                "/p3/client-runs/" + third, json=request(third), headers=headers()
            ).status_code
            == 409
        )


def test_checkpoint_fences_owner_revision_identity_and_terminal_state(configuration):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    with TestClient(Service(configuration).app()) as client:
        response = client.post(path, json=request(run_id), headers=headers())
        assert response.status_code == 201, response.text
        record = response.json()["record"]
        for changes in ({"owner_id": "other"}, {"expected_revision": 999}):
            assert (
                client.put(path, json=checkpoint(record, **changes), headers=headers()).status_code
                == 409
            )
        assert client.get(path, headers=headers(user="eve")).status_code == 404
        assert (
            client.put(path, json=checkpoint(record), headers=headers(user="eve")).status_code
            == 404
        )
        conflict = client.post(path, json=request(run_id, "weather-weekend"), headers=headers())
        assert conflict.status_code == 409
        saved = client.put(path, json=checkpoint(record), headers=headers())
        assert saved.status_code == 200
        assert client.put(path, json=checkpoint(record), headers=headers()).json() == saved.json()
        changed = client.put(path, json=checkpoint(saved.json(), "running"), headers=headers())
        assert changed.status_code == 409
        assert client.get(path, headers=headers()).json() == saved.json()


def test_invalid_snapshot_is_rejected_without_consuming_admission_slot(configuration):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    with TestClient(Service(configuration).app()) as client:
        for snapshot in (
            {"run_id": str(uuid4()), "scenario_id": "library-basic", "state": "queued"},
            {
                "run_id": run_id,
                "scenario_id": "library-basic",
                "state": "queued",
                "credential": "secret",
            },
            {
                "run_id": run_id,
                "scenario_id": "library-basic",
                "state": "queued",
                "text": "x" * 262144,
            },
        ):
            rejected = client.post(
                path, json={**request(run_id), "snapshot": snapshot}, headers=headers()
            )
            assert rejected.status_code in {400, 422}, rejected.text
        assert client.get(path, headers=headers()).status_code == 404
        assert client.post(path, json=request(run_id), headers=headers()).status_code == 201


def test_checkpoint_cannot_drop_or_rebind_prepared_operations(configuration):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    with TestClient(Service(configuration).app()) as client:
        record = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        operation = intent()
        progress = {**record["snapshot"], "state": "running", "operations": [operation]}
        prepared = client.put(path, json=checkpoint(record, snapshot=progress), headers=headers())
        assert prepared.status_code == 200, prepared.text
        row = prepared.json()
        for ops in ([], [intent("b" * 64)]):
            changed = client.put(
                path,
                json=checkpoint(row, snapshot={**progress, "operations": ops}),
                headers=headers(),
            )
            assert changed.status_code == 409, changed.text
        observed = {**operation, "phase": "observed", "status_code": 400, "job_id": "original-job"}
        completed = client.put(
            path,
            json=checkpoint(row, snapshot={**progress, "operations": [observed]}),
            headers=headers(),
        )
        assert completed.status_code == 200
        changed = client.put(
            path,
            json=checkpoint(
                completed.json(),
                snapshot={
                    **progress,
                    "operations": [{**observed, "job_id": "different-job"}],
                },
            ),
            headers=headers(),
        )
        assert changed.status_code == 409


def test_revoked_epoch_cannot_read_mutate_or_recreate_an_existing_run(configuration):
    from hashlib import sha256

    service = Service(configuration)
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    with TestClient(service.app()) as client:
        record = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        identity = service.runtime.foundation.identity
        principal = identity.authenticate("alice")
        identity.provision(
            [(sha256(b"alice").hexdigest(), principal.model_copy(update={"auth_epoch": 2}))]
        )
        assert client.get(path, headers=headers()).status_code == 403
        assert client.post(path, json=request(run_id), headers=headers()).status_code == 403
        assert client.put(path, json=checkpoint(record), headers=headers()).status_code == 403


def test_unknown_effect_cannot_release_admission_or_rewind_progress(configuration):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    operation = intent()
    with TestClient(Service(configuration).app()) as client:
        row = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        progress = {**row["snapshot"], "state": "running", "operations": [operation]}
        prepared = client.put(path, json=checkpoint(row, snapshot=progress), headers=headers())
        assert prepared.status_code == 200, prepared.text
        row = prepared.json()
        for state in ("passed", "failed", "blocked", "queued"):
            response = client.put(path, json=checkpoint(row, state), headers=headers())
            assert response.status_code == 409, (state, response.text)
            assert client.get(path, headers=headers()).json() == row
        another = str(uuid4())
        assert (
            client.post(
                "/p3/client-runs/" + another, json=request(another), headers=headers()
            ).status_code
            == 409
        )
        unknown = client.put(path, json=checkpoint(row, "unconfirmed"), headers=headers())
        assert unknown.status_code == 200, unknown.text


@pytest.mark.parametrize(
    "invalid",
    [
        {"phase": "prepared", "status_code": 200},
        {"phase": "prepared", "job_id": "premature-job"},
        {"phase": "observed"},
    ],
)
def test_inconsistent_operation_receipt_is_rejected_before_checkpoint(configuration, invalid):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    with TestClient(Service(configuration).app()) as client:
        row = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        operation = {
            "method": "POST",
            "path": "/p3/remember",
            "operation_id": "original-op",
            "request_hash": "a" * 64,
            **invalid,
        }
        response = client.put(
            path,
            json=checkpoint(row, snapshot={**row["snapshot"], "operations": [operation]}),
            headers=headers(),
        )
        assert response.status_code == 422, response.text
        assert client.get(path, headers=headers()).json() == row


def test_operation_receipt_requires_a_prior_committed_intent(configuration):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    operation = {
        **intent(),
        "phase": "observed",
        "status_code": 200,
        "job_id": "original-job",
    }
    with TestClient(Service(configuration).app()) as client:
        initial = request(run_id)
        initial["snapshot"]["operations"] = [operation]
        rejected = client.post(path, json=initial, headers=headers())
        assert rejected.status_code == 400, rejected.text
        assert client.get(path, headers=headers()).status_code == 404
        row = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        fabricated = client.put(
            path,
            json=checkpoint(
                row, snapshot={**row["snapshot"], "state": "running", "operations": [operation]}
            ),
            headers=headers(),
        )
        assert fabricated.status_code == 409, fabricated.text
        assert client.get(path, headers=headers()).json() == row


def test_postgres_online_restore_refused_preserves_registry_and_active_slot(configuration):
    from tests.integration.test_p4_demo_handoff import snapshot

    service = Service(configuration.model_copy(update={"maintenance_principals": ("alice",)}))
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    with TestClient(service.app()) as client:
        configured = client.put(
            "/p3/configuration",
            json={"snapshot": snapshot("registry_backup"), "expected_version": None},
            headers=headers(),
        )
        assert configured.status_code == 200, configured.text
        row = client.post(path, json=request(run_id), headers=headers()).json()["record"]
        operation = intent()
        saved = client.put(
            path,
            json=checkpoint(
                row,
                snapshot={**row["snapshot"], "state": "unconfirmed", "operations": [operation]},
            ),
            headers=headers(),
        )
        assert saved.status_code == 200, saved.text
        row = saved.json()
        backup = client.post("/p3/backups", json={"backup_id": "registry"}, headers=headers())
        assert backup.status_code == 400, backup.text
        assert backup.json()["code"] == "CONTRACT_VIOLATION"
        restore = client.post(
            "/p3/restore-drills",
            json={"backup_id": "registry", "restore_id": "registry_drill"},
            headers=headers(),
        )
        assert restore.status_code == 400, restore.text
        assert restore.json()["code"] == "CONTRACT_VIOLATION"
        duplicate = client.post(path, json=request(run_id, owner="new"), headers=headers())
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["created"] is False
        assert duplicate.json()["record"] == row
        another_id = str(uuid4())
        another = client.post(
            "/p3/client-runs/" + another_id, json=request(another_id), headers=headers()
        )
        assert another.status_code == 409, another.text
        assert client.get(path, headers=headers()).json() == row
