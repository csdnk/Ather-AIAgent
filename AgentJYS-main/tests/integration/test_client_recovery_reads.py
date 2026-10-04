"""Recovery reads expose original bytes without confirming or publishing them."""

import json
from copy import deepcopy
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_inputs import owner_headers
from tests.integration.test_client_recoveries import held, recovery_path, recovery_request
from tests.integration.test_client_states import running, state
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers
from tests.integration.test_transfer_write_boundaries import original_object

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_definitions import ClientDefinitions
from aether_agent_memory.runtime.foundation.client_inputs import ClientInputs
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.client_states import ClientStates
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from azure_component_service import Service

pytestmark = pytest.mark.integration


def read_params(record):
    return {
        "expected_owner_id": record["owner_id"],
        "expected_revision": record["revision"],
        "expected_record_hash": fingerprint(record),
    }


def recovery_object(service, client, monkeypatch, kind, *, ready=False):
    if kind == "state":
        record = running(client)
        payload = json.dumps(state(record)).encode()
        path = f"/p3/client-runs/{record['run_id']}/states/1"
    else:
        record, path, payload = original_object(client, kind)
    original = PostgresTransaction.put_if_revision
    object_type = {
        "definition": "client_run_definition",
        "input": "client_request_input",
        "state": "client_run_state",
    }[kind]

    def interrupt_confirmation(tx, ref, value, revision):
        if ref.object_type == object_type and value["state"] == "ready":
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "interrupted confirmation")
        return original(tx, ref, value, revision)

    with monkeypatch.context() as patch:
        if not ready:
            patch.setattr(PostgresTransaction, "put_if_revision", interrupt_confirmation)
        response = client.put(path, content=payload, headers=owner_headers(record))
    assert response.status_code == (200 if ready else 503), response.text
    if kind == "state" and ready:
        record = response.json()
    foundation = service.runtime.foundation
    ctx, run_id = foundation.identity.context("alice"), UUID(record["run_id"])
    if kind == "definition":
        ref = ClientDefinitions.ref(ctx, run_id)
    elif kind == "input":
        ref = ClientInputs.reference(ctx, run_id, "original-input")
    else:
        ref = ClientStates.ref(ctx, run_id, 1)
    with foundation.uow.transaction() as tx:
        metadata = deepcopy(tx.get(ref))
    moved = held(client, record)
    suffix = path.split(str(run_id), 1)[1]
    return moved, path, recovery_path(moved) + suffix, payload, ref, metadata


@pytest.mark.parametrize("kind", ["definition", "input", "state"])
@pytest.mark.parametrize("ready", [False, True])
def test_recovery_read_preserves_original_pending_or_ready_object(
    configuration, monkeypatch, kind, ready
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, ordinary, path, payload, ref, metadata = recovery_object(
            service, client, monkeypatch, kind, ready=ready
        )
        before = client.get(ordinary, headers=headers())
        assert before.status_code == (200 if ready else 400)
        if not ready:
            assert before.json()["code"] == "COMMIT_UNCONFIRMED"
        response = client.get(path, params=read_params(record), headers=headers())
        assert response.status_code == 200, response.text
        assert response.content == payload
        evidence = json.loads(response.headers["X-P3-Recovery-Object"])
        assert evidence["state"] == ("ready" if ready else "pending")
        assert evidence["object_revision"] == metadata["revision"]
        assert evidence["record_hash"] == fingerprint(record)
        assert evidence["run_id"] == record["run_id"]
        assert evidence["transfer_id"] == "recovery-1" and evidence["kind"] == kind
        assert response.headers["Cache-Control"] == "no-store"
        assert client.get(ordinary, headers=headers()).status_code == before.status_code
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.get(ref) == metadata
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record
        assert client.get(recovery_path(record), headers=headers()).json()["state"] == "unconfirmed"


@pytest.mark.parametrize("kind", ["definition", "input", "state"])
@pytest.mark.parametrize("fault", [None, b"corrupt", "unavailable"])
def test_recovery_read_cannot_reconstruct_missing_or_corrupt_original_bytes(
    configuration, monkeypatch, kind, fault
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path, payload, _, _ = recovery_object(service, client, monkeypatch, kind)
        objects = service.execution.inputs.objects
        original = objects.get_object_sync

        def broken(key):
            value = original(key)
            if value != payload:
                return value
            if fault == "unavailable":
                raise OSError("injected P2 read failure")
            return fault

        monkeypatch.setattr(objects, "get_object_sync", broken)
        response = client.get(path, params=read_params(record), headers=headers())
        expected = (
            "COMMIT_UNCONFIRMED"
            if fault is None
            else "DEPENDENCY_UNAVAILABLE"
            if fault == "unavailable"
            else "CONTRACT_VIOLATION"
        )
        assert response.status_code == (503 if fault == "unavailable" else 400), response.text
        assert response.json()["code"] == expected


@pytest.mark.parametrize("kind", ["definition", "input", "state"])
@pytest.mark.parametrize("fault", ["owner", "revision", "hash", "transfer", "identity"])
def test_recovery_read_rejects_stale_expectation_before_object_io(
    configuration, monkeypatch, kind, fault
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path, _, _, _ = recovery_object(service, client, monkeypatch, kind)
        params, credentials = read_params(record), headers()
        if fault == "owner":
            params["expected_owner_id"] = "wrong"
        elif fault == "revision":
            params["expected_revision"] += 1
        elif fault == "hash":
            params["expected_record_hash"] = "0" * 64
        elif fault == "transfer":
            path = path.replace("recovery-1", "wrong")
        else:
            credentials = headers(user="eve")

        def forbidden_io(*args):
            pytest.fail("stale recovery must not reach P2")

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", forbidden_io)
        response = client.get(path, params=params, headers=credentials)
        assert response.status_code == (404 if fault == "identity" else 409), response.text


@pytest.mark.parametrize("kind", ["definition", "input", "state"])
@pytest.mark.parametrize("fault", ["identity", "record", "reservation", "hold_release"])
def test_recovery_read_revalidates_after_p2_io(configuration, monkeypatch, kind, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path, payload, ref, metadata = recovery_object(
            service, client, monkeypatch, kind
        )
        foundation, objects = service.runtime.foundation, service.execution.inputs.objects
        original = objects.get_object_sync

        def change_after_read(key):
            result = original(key)
            if result == payload:
                ctx = foundation.identity.context("alice")
                with foundation.uow.transaction() as tx:
                    if fault == "identity":
                        row = tx.read("identities", "alice")
                        tx.write("identities", "alice", {**row, "enabled": False})
                    elif fault == "reservation":
                        changed = {**metadata, "revision": metadata["revision"] + 1}
                        tx.put_if_revision(ref, changed, metadata["revision"])
                    else:
                        changed = deepcopy(record)
                        if fault == "hold_release":
                            changed["ownership"]["recovery_transfer_id"] = None
                        else:
                            changed["snapshot"]["error"] = {"code": "changed during read"}
                        changed["revision"] += 1
                        tx.put_if_revision(
                            ClientRuns.ref(ctx, UUID(record["run_id"])), changed, record["revision"]
                        )
            return result

        monkeypatch.setattr(objects, "get_object_sync", change_after_read)
        response = client.get(path, params=read_params(record), headers=headers())
        assert response.status_code == (403 if fault == "identity" else 409), response.text
        assert response.content != payload


def test_recovery_read_is_unavailable_after_explicit_activation(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path, _, _, _ = recovery_object(service, client, monkeypatch, "definition")
        activated = client.post(
            recovery_path(record), json=recovery_request(record, target="queued"), headers=headers()
        )
        assert activated.status_code == 201, activated.text
        response = client.get(path, params=read_params(record), headers=headers())
        assert response.status_code == 409, response.text


@pytest.mark.parametrize(
    "fault", ["active", "transfer_missing", "transfer_corrupt", "metadata_corrupt"]
)
def test_recovery_read_requires_intact_hold_and_object_evidence(configuration, monkeypatch, fault):
    from aether_agent_memory.runtime.foundation.client_transfers import ClientTransfers

    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path, _, ref, metadata = recovery_object(service, client, monkeypatch, "state")
        foundation = service.runtime.foundation
        ctx, run_id = foundation.identity.context("alice"), UUID(record["run_id"])
        with foundation.uow.transaction() as tx:
            if fault == "active":
                index_ref = ClientRuns.ref(ctx, None)
                value = tx.get(index_ref)
                tx.put_if_revision(
                    index_ref,
                    {**value, "active": None, "revision": value["revision"] + 1},
                    value["revision"],
                )
            elif fault == "metadata_corrupt":
                tx.put_if_revision(ref, {**metadata, "state": "unknown"}, metadata["revision"])
            else:
                transfer_ref = ClientTransfers.ref(ctx, run_id, "recovery-1")
                if fault == "transfer_missing":
                    tx.remove_if_revision(transfer_ref, 1)
                else:
                    value = tx.get(transfer_ref)
                    value["record"]["owner_id"] = "different"
                    tx.put_if_revision(transfer_ref, value, 1)
        response = client.get(path, params=read_params(record), headers=headers())
        assert response.status_code == (409 if fault == "active" else 400), response.text
        assert response.json()["code"] == (
            "VERSION_CONFLICT" if fault == "active" else "CONTRACT_VIOLATION"
        )


def test_recovery_reads_keep_old_pending_stream_distinct_from_confirmed_head(
    configuration, monkeypatch
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        first, ordinary, _, original_bytes, ref, metadata = recovery_object(
            service, client, monkeypatch, "state"
        )
        response = client.post(
            recovery_path(first), json=recovery_request(first), headers=headers()
        )
        assert response.status_code == 201, response.text
        second = held(
            client, response.json()["record"], transfer_id="recovery-2", owner="next-owner"
        )
        path = recovery_path(second) + "/states/1"
        old = client.get(path, params=read_params(second), headers=headers())
        confirmed = client.get(
            path, params={**read_params(second), "stream_id": "recovery-1"}, headers=headers()
        )
        assert old.status_code == confirmed.status_code == 200
        assert old.content == original_bytes
        assert json.loads(old.headers["X-P3-Recovery-Object"])["state"] == "pending"
        assert json.loads(confirmed.headers["X-P3-Recovery-Object"])["state"] == "ready"
        assert confirmed.content != old.content
        assert client.get(ordinary, headers=headers()).json()["code"] == "COMMIT_UNCONFIRMED"
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.get(ref) == metadata


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_recovery_read_requires_recover_permission_in_addition_to_diagnose(configuration, method):
    import yaml
    from tests.integration.test_client_transfers import transfer_request

    from aether_agent_memory.runtime.contracts.client_transfers import ClientRunTransferResult
    from aether_agent_memory.runtime.foundation.client_transfers import ClientTransfers

    raw = yaml.safe_load(configuration.identity_file.read_text("utf-8"))
    identity = next(
        item for item in raw["identities"] if item["principal"]["principal_id"] == "alice"
    )
    identity["principal"]["permissions"].remove("maintenance:recover")
    configuration.identity_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    service = Service(configuration)
    with TestClient(service.app()) as client:
        original, ordinary, payload = original_object(client, "definition")
        assert (
            client.put(ordinary, content=payload, headers=owner_headers(original)).status_code
            == 200
        )
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
        assert client.get(ordinary, headers=headers()).status_code == 200
        refused = client.request(
            method,
            recovery_path(record) + "/definition",
            params=read_params(record),
            headers=headers(),
        )
        assert refused.status_code == 403, refused.text


def test_recovery_state_with_matching_hash_but_foreign_envelope_is_rejected(
    configuration, monkeypatch
):
    from hashlib import sha256

    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path, payload, ref, metadata = recovery_object(
            service, client, monkeypatch, "state"
        )
        body = json.loads(payload)
        body["scenario_id"] = "foreign"
        changed = json.dumps(body).encode()
        binding = {
            **metadata["binding"],
            "content_hash": sha256(changed).hexdigest(),
            "size_bytes": len(changed),
        }
        with service.runtime.foundation.uow.transaction() as tx:
            tx.put_if_revision(ref, {**metadata, "binding": binding}, metadata["revision"])
        service.execution.inputs.objects.put_object_sync(
            ClientStates.key(ref, binding["content_hash"]), changed
        )
        response = client.get(path, params=read_params(record), headers=headers())
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "CONTRACT_VIOLATION"
