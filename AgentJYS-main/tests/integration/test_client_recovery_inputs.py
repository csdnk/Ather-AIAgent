"""Confirm only verified original input bytes while retaining recovery ownership."""

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_recoveries import recovery_path
from tests.integration.test_client_recovery_reads import read_params, recovery_object
from tests.integration.test_client_transfers import transfer_path, transfer_request
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually, headers

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_inputs import StoredClientInput
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from azure_component_service import Service

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("ready", [False, True])
def test_confirm_original_input_preserves_writer_bytes_journal_and_hold(
    configuration, monkeypatch, ready
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        record, ordinary, path, payload, ref, before = recovery_object(
            service, client, monkeypatch, "input", ready=ready
        )
        response = client.put(path, params=read_params(record), headers=headers())
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["run_id"] == record["run_id"]
        assert receipt["operation_id"] == "original-input" and receipt["state"] == "ready"
        assert receipt["size_bytes"] == len(payload)
        assert client.get(ordinary, headers=headers()).content == payload
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.get(ref) == {
                **before,
                "state": "ready",
                "revision": before["revision"] + (not ready),
            }
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record
        assert client.get(recovery_path(record), headers=headers()).json()["state"] == "unconfirmed"
        assert client.put(path, params=read_params(record), headers=headers()).json() == receipt


@pytest.mark.parametrize("fault", ["missing", "corrupt", "unavailable", "confirmation"])
def test_input_confirmation_keeps_pending_after_missing_bytes_or_failed_commit(
    configuration, monkeypatch, fault
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        record, ordinary, path, payload, ref, before = recovery_object(
            service, client, monkeypatch, "input"
        )
        objects = service.execution.inputs.objects
        get, put = objects.get_object_sync, PostgresTransaction.put_if_revision

        def read(key):
            data = get(key)
            if data == payload and fault != "confirmation":
                if fault == "unavailable":
                    raise OSError("injected object outage")
                return None if fault == "missing" else b"corrupt"
            return data

        def commit(tx, target, value, revision):
            if fault == "confirmation" and target == ref and value["state"] == "ready":
                raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "injected commit outage")
            return put(tx, target, value, revision)

        monkeypatch.setattr(objects, "get_object_sync", read)
        monkeypatch.setattr(PostgresTransaction, "put_if_revision", commit)
        response = client.put(path, params=read_params(record), headers=headers())
        assert response.status_code == (503 if fault in {"unavailable", "confirmation"} else 400)
        assert (
            response.json()["code"]
            == {
                "missing": "COMMIT_UNCONFIRMED",
                "corrupt": "CONTRACT_VIOLATION",
                "unavailable": "DEPENDENCY_UNAVAILABLE",
                "confirmation": "DEPENDENCY_UNAVAILABLE",
            }[fault]
        )
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.get(ref) == before
        assert client.get(ordinary, headers=headers()).json()["code"] == "COMMIT_UNCONFIRMED"
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record


def test_input_confirmation_cannot_publish_after_owner_changes_during_object_read(
    configuration, monkeypatch
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        record, _, path, payload, ref, before = recovery_object(
            service, client, monkeypatch, "input"
        )
        get = service.execution.inputs.objects.get_object_sync
        moved = False

        def read(key):
            nonlocal moved
            data = get(key)
            if data == payload and not moved:
                moved = True
                response = client.post(
                    transfer_path(record, "next-owner"),
                    json=transfer_request(record, "worker-c"),
                    headers=headers(),
                )
                assert response.status_code == 201, response.text
            return data

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", read)
        response = client.put(path, params=read_params(record), headers=headers())
        assert response.status_code == 409 and response.json()["code"] == "VERSION_CONFLICT"
        with service.runtime.foundation.uow.transaction() as tx:
            assert StoredClientInput.model_validate(tx.get(ref)).writer_id == before["writer_id"]
            assert tx.get(ref)["state"] == "pending"
