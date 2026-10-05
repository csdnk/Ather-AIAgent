"""Transfer serializes with original admission and fences publication after P2 IO."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_admission import admission_headers
from tests.integration.test_client_scopes import prepare_operation, remember_body
from tests.integration.test_client_states import running, save, state
from tests.integration.test_client_transfers import prepared, transfer_path, transfer_request
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually, headers

from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from azure_component_service import Service

pytestmark = pytest.mark.integration


def business(client):
    record = running(client)
    payload = json.dumps(remember_body(record)).encode()
    record = prepare_operation(client, record, payload, "/p3/remember")
    response, _, _ = save(client, record, state(record))
    assert response.status_code == 200
    return response.json(), payload


def test_admission_committed_before_transfer_keeps_original_temporal_job(
    configuration, monkeypatch
):
    entered, release, attempted = Event(), Event(), Event()
    admit = ExecutionLedger.admit

    def pause_admission(ledger, tx, ctx, spec, **kwargs):
        result = admit(ledger, tx, ctx, spec, **kwargs)
        if ctx.operation_id == "managed-op" and kwargs.get("http_request") is not None:
            entered.set()
            assert release.wait(10)
        return result

    service = Service(configuration)
    with TestClient(service.app()) as client, ThreadPoolExecutor(2) as pool:
        record, payload = business(client)
        monkeypatch.setattr(ExecutionLedger, "admit", pause_admission)
        original = pool.submit(
            client.post,
            "/p3/remember",
            content=payload,
            headers=admission_headers(record, "managed-op"),
        )

        def transfer():
            attempted.set()
            return client.post(
                transfer_path(record), json=transfer_request(record), headers=headers()
            )

        try:
            assert entered.wait(10)
            handoff = pool.submit(transfer)
            assert attempted.wait(10) and not handoff.done()
        finally:
            release.set()
        accepted = original.result(timeout=15)
        moved = handoff.result(timeout=15)
        assert "X-P3-Job-ID" in accepted.headers, accepted.text
        assert moved.status_code == 201, moved.text
        job = accepted.headers["X-P3-Job-ID"]
        result = eventually(
            lambda: (
                value.json()
                if (
                    value := client.get(f"/p3/operations/{job}/result", headers=headers())
                ).status_code
                == 200
                else None
            )
        )
        assert result["operation_id"] == "managed-op" and result["saved"]
        found = client.get(
            "/p3/operation-requests/managed-op?kind=remember.save", headers=headers()
        ).json()
        assert found["job_id"] == job and found["http_request"]["client_run"] == {
            "run_id": record["run_id"],
            "owner_id": record["owner_id"],
            "revision": record["revision"],
        }
        assert (
            client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json()
            == moved.json()["record"]
        )


def test_transfer_committed_first_rejects_waiting_old_admission(configuration, monkeypatch):
    entered, release, attempted = Event(), Event(), Event()
    put = PostgresTransaction.put_if_revision

    def pause_receipt(tx, ref, value, revision):
        result = put(tx, ref, value, revision)
        if ref.object_type == "client_run_transfer":
            entered.set()
            assert release.wait(10)
        return result

    with TestClient(Service(configuration).app()) as client, ThreadPoolExecutor(2) as pool:
        record, payload = business(client)
        monkeypatch.setattr(PostgresTransaction, "put_if_revision", pause_receipt)
        handoff = pool.submit(
            client.post, transfer_path(record), json=transfer_request(record), headers=headers()
        )

        def send_old():
            attempted.set()
            return client.post(
                "/p3/remember", content=payload, headers=admission_headers(record, "managed-op")
            )

        try:
            assert entered.wait(10)
            original = pool.submit(send_old)
            assert attempted.wait(10) and not original.done()
        finally:
            release.set()
        assert handoff.result(timeout=15).status_code == 201
        rejected = original.result(timeout=15)
        assert rejected.status_code == 409 and "X-P3-Job-ID" not in rejected.headers
        assert (
            client.get(
                "/p3/operation-requests/managed-op?kind=remember.save", headers=headers()
            ).json()["state"]
            == "unconfirmed"
        )


def test_old_state_object_write_cannot_publish_after_transfer(configuration, monkeypatch):
    entered, release = Event(), Event()
    service = Service(configuration)
    with TestClient(service.app()) as client, ThreadPoolExecutor(1) as pool:
        record = prepared(client)
        objects = service.execution.inputs.objects
        put = objects.put_object_sync

        def pause_put(key, payload):
            put(key, payload)
            if "/client-states/" in key:
                entered.set()
                assert release.wait(10)

        monkeypatch.setattr(objects, "put_object_sync", pause_put)
        pending = pool.submit(save, client, record, state(record))
        try:
            assert entered.wait(10)
            moved = client.post(
                transfer_path(record), json=transfer_request(record), headers=headers()
            )
            assert moved.status_code == 201, moved.text
        finally:
            release.set()
        rejected, payload, path = pending.result(timeout=15)
        assert rejected.status_code == 409, rejected.text
        assert (
            client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json()
            == moved.json()["record"]
        )
        stored = client.get(path, headers=headers())
        assert stored.status_code == 400 and stored.json()["code"] == "COMMIT_UNCONFIRMED"
        assert moved.json()["record"]["execution_state"] == record["execution_state"]
