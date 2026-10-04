"""Activation cannot release a stale hold or return inconsistent original evidence."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from tests.integration.test_client_recoveries import held, recovery_path, recovery_request
from tests.integration.test_client_transfers import prepared
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_recoveries import ClientRecoveries
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.client_states import ClientStates
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from azure_component_service import Service

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "revision",
        "hash",
        "transfer",
        "stream",
        "parent",
        "parent_stream",
        "definition",
        "queued",
        "active",
    ],
)
def test_stale_recovery_cannot_reserve_state_or_release_hold(configuration, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = held(client, prepared(client))
        body, path = recovery_request(record), recovery_path(record)
        if fault == "owner":
            body["expected_owner_id"] = "someone-else"
        elif fault == "revision":
            body["expected_revision"] += 1
        elif fault == "hash":
            body["expected_record_hash"] = "0" * 64
        elif fault == "transfer":
            path = recovery_path(record, "not-the-hold")
        elif fault == "stream":
            body["execution_state"]["stream_id"] = "not-the-hold"
        elif fault == "parent":
            body["execution_state"]["parent_hash"] = "0" * 64
        elif fault == "parent_stream":
            body["execution_state"]["parent_stream_id"] = "not-the-parent"
        elif fault == "definition":
            body["execution_state"]["definition_hash"] = "0" * 64
        elif fault == "queued":
            body = recovery_request(record, target="queued")
        else:
            foundation = service.runtime.foundation
            ref = ClientRuns.ref(foundation.identity.context("alice"), None)
            with foundation.uow.transaction() as tx:
                index = tx.get(ref)
                tx.put_if_revision(
                    ref,
                    {**index, "active": None, "revision": index["revision"] + 1},
                    index["revision"],
                )
        refused = client.post(path, json=body, headers=headers())
        assert refused.status_code == 409, refused.text
        assert client.get(path, headers=headers()).json()["state"] == "unconfirmed"
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record
        pending = client.get(
            f"/p3/client-runs/{record['run_id']}/states/2?stream_id=recovery-1", headers=headers()
        )
        assert pending.status_code == 404


@pytest.mark.parametrize("fault", ["epoch", "identity", "unauthenticated"])
def test_activation_revalidates_identity_and_recover_permission(configuration, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = held(client, prepared(client))
        body, path = recovery_request(record), recovery_path(record)
        credentials = headers()
        if fault == "epoch":
            raw = yaml.safe_load(configuration.identity_file.read_text("utf-8"))
            raw["revision"] += 1
            for identity in raw["identities"]:
                identity["principal"]["auth_epoch"] += 1
            configuration.identity_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
            service.reload_identity()
        elif fault == "identity":
            credentials = headers(user="eve")
        else:
            credentials = {}
        refused = client.post(path, json=body, headers=credentials)
        assert refused.status_code == {"epoch": 403, "identity": 404, "unauthenticated": 401}[fault]


def test_read_permission_alone_does_not_allow_activation(configuration):
    from tests.integration.test_client_transfers import transfer_request

    from aether_agent_memory.runtime.contracts.client_transfers import ClientRunTransferResult
    from aether_agent_memory.runtime.foundation.client_transfers import ClientTransfers

    raw = yaml.safe_load(configuration.identity_file.read_text("utf-8"))
    identity = next(
        value for value in raw["identities"] if value["principal"]["principal_id"] == "alice"
    )
    identity["principal"]["permissions"].remove("maintenance:recover")
    configuration.identity_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    service = Service(configuration)
    with TestClient(service.app()) as client:
        original = prepared(client)
        # Seed a validated held record to isolate RECOVER from the epoch gate.
        # Runtime permission revocation advances the epoch and is tested above.
        record = deepcopy(original)
        record.update(owner_id="recoverer", revision=original["revision"] + 1)
        record["ownership"]["epochs"].append(
            {
                "owner_id": "recoverer",
                "first_revision": record["revision"],
                "transfer_id": "recovery-1",
            }
        )
        record["ownership"]["recovery_transfer_id"] = "recovery-1"
        receipt = ClientRunTransferResult(
            run_id=record["run_id"],
            transfer_id="recovery-1",
            request=transfer_request(original, "recoverer"),
            previous=original,
            record=record,
        )
        foundation = service.runtime.foundation
        ctx = foundation.identity.context("alice")
        with foundation.uow.transaction() as tx:
            tx.put_if_revision(ClientRuns.ref(ctx, receipt.run_id), record, original["revision"])
            tx.put_if_revision(
                ClientTransfers.ref(ctx, receipt.run_id, "recovery-1"),
                receipt.model_dump(mode="json"),
                None,
            )
        path = recovery_path(record)
        assert client.get(path, headers=headers()).status_code == 200
        refused = client.post(path, json=recovery_request(record), headers=headers())
        assert refused.status_code == 403, refused.text
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record


def test_activation_receipt_failure_rolls_back_head_hold_and_ready_state(
    configuration, monkeypatch
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = held(client, prepared(client))
        body, path = recovery_request(record), recovery_path(record)
        put = PostgresTransaction.put_if_revision

        def fail_final_receipt(tx, ref, value, revision):
            result = put(tx, ref, value, revision)
            if ref.object_type == "client_run_recovery" and value["result"] is not None:
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "injected final receipt failure"
                )
            return result

        with monkeypatch.context() as patch:
            patch.setattr(PostgresTransaction, "put_if_revision", fail_final_receipt)
            response = client.post(path, json=body, headers=headers())
        assert response.status_code == 503, response.text
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record
        assert client.get(path, headers=headers()).json()["state"] == "unconfirmed"
        foundation = service.runtime.foundation
        ref = ClientStates.ref(
            foundation.identity.context("alice"), UUID(record["run_id"]), 2, "recovery-1"
        )
        with foundation.uow.transaction() as tx:
            pending = tx.get(ref)
        assert pending["state"] == "pending" and pending["revision"] == 1
        key = ClientStates.key(ref, pending["binding"]["content_hash"])
        payload = service.execution.inputs.objects.get_object_sync(key)
        assert payload is not None
        completed = client.post(path, json=body, headers=headers())
        assert completed.status_code == 201, completed.text
        assert completed.json()["record"]["revision"] == record["revision"] + 1
        assert service.execution.inputs.objects.get_object_sync(key) == payload


def test_transfer_during_recovery_object_io_cannot_activate_old_owner(configuration, monkeypatch):
    service = Service(configuration)
    entered, release = Event(), Event()
    with TestClient(service.app()) as client, ThreadPoolExecutor(1) as pool:
        record = held(client, prepared(client))
        body, path = recovery_request(record), recovery_path(record)
        objects = service.execution.inputs.objects
        put = objects.put_object_sync

        def pause_put(key, payload):
            put(key, payload)
            if "/client-states/" in key:
                entered.set()
                assert release.wait(10)

        with monkeypatch.context() as patch:
            patch.setattr(objects, "put_object_sync", pause_put)
            future = pool.submit(client.post, path, json=body, headers=headers())
            try:
                assert entered.wait(10)
                current = held(client, record, "recovery-2", "next-owner")
            finally:
                release.set()
            response = future.result(timeout=15)
        assert response.status_code == 409, response.text
        assert (
            client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == current
        )
        assert client.get(path, headers=headers()).json()["state"] == "unconfirmed"
        assert (
            client.post(
                recovery_path(current), json=recovery_request(current), headers=headers()
            ).status_code
            == 201
        )


def test_original_activation_receipt_survives_restart_and_cannot_rewind_later_owner(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = held(client, prepared(client))
        body, path = recovery_request(record), recovery_path(record)
        response = client.post(path, json=body, headers=headers())
        assert response.status_code == 201
        original_result = response.json()
        current = held(client, original_result["record"], "recovery-2", "next-owner")
    with TestClient(Service(configuration).app()) as client:
        assert client.get(path, headers=headers()).json()["result"] == original_result
        replay = client.post(path, json=body, headers=headers())
        assert replay.status_code == 200 and replay.json() == original_result
        assert (
            client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == current
        )


@pytest.mark.parametrize("fault", ["request", "revision", "missing_result", "malformed_result"])
def test_corrupt_activation_metadata_cannot_be_returned_as_original_evidence(configuration, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = held(client, prepared(client))
        body, path = recovery_request(record), recovery_path(record)
        completed = client.post(path, json=body, headers=headers())
        assert completed.status_code == 201
        foundation = service.runtime.foundation
        ref = ClientRecoveries.ref(
            foundation.identity.context("alice"), UUID(record["run_id"]), "recovery-1"
        )
        with foundation.uow.transaction() as tx:
            raw = tx.get(ref)
            broken = deepcopy(raw)
            if fault == "request":
                broken["request"]["expected_record_hash"] = "0" * 64
            elif fault == "revision":
                broken["revision"] = 7
            elif fault == "missing_result":
                broken["result"] = None
            else:
                broken["result"]["record"]["snapshot"]["error"] = {"unexpected": "invented"}
            tx.put_if_revision(ref, broken, raw["revision"])
        for response in (
            client.get(path, headers=headers()),
            client.post(path, json=body, headers=headers()),
        ):
            assert response.status_code == 400, response.text
            assert response.json()["code"] == "CONTRACT_VIOLATION"
        assert (
            client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json()
            == completed.json()["record"]
        )
