"""Confirm original initialization bytes while the recovery hold still fences writers."""

from copy import deepcopy
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_recoveries import held, recovery_path
from tests.integration.test_client_recovery_reads import read_params, recovery_object
from tests.integration.test_client_run_registry import checkpoint
from tests.integration.test_client_states import running, save, state
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers
from tests.integration.test_transfer_write_boundaries import original_object

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_definitions import ClientDefinitions
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from azure_component_service import Service

pytestmark = pytest.mark.integration


def confirm(client, record, payload=None, **kwargs):
    return client.put(
        recovery_path(record) + "/definition",
        params=read_params(record),
        content=payload,
        headers=headers(),
        **kwargs,
    )


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("send_original", [False, True])
def test_confirmation_preserves_hold_binding_writer_and_run(
    configuration, monkeypatch, ready, send_original
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, ordinary, path, payload, ref, before = recovery_object(
            service, client, monkeypatch, "definition", ready=ready
        )
        response = confirm(client, record, payload if send_original else None)
        assert response.status_code == 200, response.text
        assert response.json() == {
            **record["definition"],
            "run_id": record["run_id"],
            "state": "ready",
        }
        assert client.get(ordinary, headers=headers()).content == payload
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.get(ref) == {
                **before,
                "state": "ready",
                "revision": before["revision"] + (not ready),
            }
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record
        assert client.get(recovery_path(record), headers=headers()).json()["state"] == "unconfirmed"
        assert confirm(client, record).json() == response.json()
        assert client.get(path, params=read_params(record), headers=headers()).status_code == 200


@pytest.mark.parametrize("send_original", [False, True])
def test_missing_reservation_needs_original_bytes_and_records_actual_writer(
    configuration, send_original
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        original, ordinary, payload = original_object(client, "definition")
        record = held(client, original)
        response = confirm(client, record, payload if send_original else None)
        assert response.status_code == (200 if send_original else 400), response.text
        ctx = service.runtime.foundation.identity.context("alice")
        with service.runtime.foundation.uow.transaction() as tx:
            stored = tx.get(ClientDefinitions.ref(ctx, UUID(record["run_id"])))
        if send_original:
            assert stored["writer_id"] == record["owner_id"]
            assert stored["state"] == "ready" and stored["revision"] == 2
            assert client.get(ordinary, headers=headers()).content == payload
        else:
            assert response.json()["code"] == "COMMIT_UNCONFIRMED"
            assert stored is None


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("fault", ["missing", "corrupt", "unavailable"])
def test_exact_payload_cannot_repair_ready_or_corrupt_objects(
    configuration, monkeypatch, ready, fault
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, _, payload, ref, before = recovery_object(
            service, client, monkeypatch, "definition", ready=ready
        )
        objects = service.execution.inputs.objects
        original_get, original_put = objects.get_object_sync, objects.put_object_sync
        restored, writes = False, []

        def get(key):
            value = original_get(key)
            if value != payload or restored:
                return value
            if fault == "unavailable":
                raise OSError("original bytes unavailable")
            return None if fault == "missing" else b"corrupt"

        def put(key, value):
            nonlocal restored
            writes.append(value)
            original_put(key, value)
            restored = True

        monkeypatch.setattr(objects, "get_object_sync", get)
        monkeypatch.setattr(objects, "put_object_sync", put)
        response = confirm(client, record, payload)
        success = not ready and fault == "missing"
        assert response.status_code == (
            200 if success else 503 if fault == "unavailable" else 400
        ), response.text
        assert writes == ([payload] if success else [])
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.get(ref) == (
                {**before, "state": "ready", "revision": 2} if success else before
            )
        if not success:
            assert response.json()["code"] == (
                "DEPENDENCY_UNAVAILABLE" if fault == "unavailable" else "CONTRACT_VIOLATION"
            )


def test_missing_pending_bytes_without_original_remain_unconfirmed(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, _, _, ref, before = recovery_object(service, client, monkeypatch, "definition")
        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", lambda key: None)
        response = confirm(client, record)
        assert response.status_code == 400 and response.json()["code"] == "COMMIT_UNCONFIRMED", (
            response.text
        )
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.get(ref) == before


@pytest.mark.parametrize("fault", ["payload", "owner", "revision", "hash", "transfer", "identity"])
def test_confirmation_rejects_wrong_intent_before_p2(configuration, monkeypatch, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path, payload, _, _ = recovery_object(service, client, monkeypatch, "definition")
        params, credentials = read_params(record), headers()
        if fault == "payload":
            payload += b"changed"
        elif fault == "owner":
            params["expected_owner_id"] = "wrong"
        elif fault == "revision":
            params["expected_revision"] += 1
        elif fault == "hash":
            params["expected_record_hash"] = "0" * 64
        elif fault == "transfer":
            path = path.replace("recovery-1", "wrong")
        else:
            credentials = headers(user="eve")

        def forbidden(*args):
            pytest.fail("invalid intent must not reach P2")

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", forbidden)
        response = client.put(path, params=params, content=payload, headers=credentials)
        assert response.status_code == (404 if fault == "identity" else 409), response.text


@pytest.mark.parametrize("fault", ["identity", "hold_release", "record", "reservation"])
def test_confirmation_revalidates_after_p2_read(configuration, monkeypatch, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, _, payload, ref, before = recovery_object(
            service, client, monkeypatch, "definition"
        )
        foundation, objects = service.runtime.foundation, service.execution.inputs.objects
        original = objects.get_object_sync
        changed_once = False

        def change(key):
            nonlocal changed_once
            value = original(key)
            if value == payload and not changed_once:
                changed_once = True
                ctx = foundation.identity.context("alice")
                with foundation.uow.transaction() as tx:
                    if fault == "identity":
                        identity = tx.read("identities", "alice")
                        tx.write("identities", "alice", {**identity, "enabled": False})
                    elif fault == "reservation":
                        tx.put_if_revision(
                            ref, {**before, "revision": before["revision"] + 1}, before["revision"]
                        )
                    else:
                        changed = deepcopy(record)
                        if fault == "hold_release":
                            changed["ownership"]["recovery_transfer_id"] = None
                        else:
                            changed["snapshot"]["error"] = {"code": "changed during IO"}
                        changed["revision"] += 1
                        tx.put_if_revision(
                            ClientRuns.ref(ctx, UUID(record["run_id"])), changed, record["revision"]
                        )
            return value

        monkeypatch.setattr(objects, "get_object_sync", change)
        response = confirm(client, record)
        assert response.status_code == (403 if fault == "identity" else 409), response.text
        with foundation.uow.transaction() as tx:
            assert tx.get(ref)["state"] == "pending"


def test_confirmation_transaction_failure_leaves_pending_and_hold(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, _, _, ref, before = recovery_object(service, client, monkeypatch, "definition")
        original = PostgresTransaction.put_if_revision

        def fail(tx, target, value, revision):
            if target == ref and value["state"] == "ready":
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "confirmation transaction failed"
                )
            return original(tx, target, value, revision)

        monkeypatch.setattr(PostgresTransaction, "put_if_revision", fail)
        response = confirm(client, record)
        assert response.status_code == 503, response.text
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.get(ref) == before
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record


@pytest.mark.parametrize("started", ["journal", "head", "running", "unconfirmed"])
def test_initialization_confirmation_distinguishes_before_business_from_started(
    configuration, monkeypatch, started
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        if started == "journal":
            record, _, _ = original_object(client, "input")
        else:
            record = running(client)
        if started == "head":
            response, _, _ = save(client, record, state(record))
            assert response.status_code == 200
            record = response.json()
        elif started == "unconfirmed":
            response = client.put(
                f"/p3/client-runs/{record['run_id']}",
                json=checkpoint(
                    record,
                    snapshot={
                        **record["snapshot"],
                        "state": "unconfirmed",
                        "error": {"code": "initial state IO failed"},
                    },
                ),
                headers=headers(),
            )
            assert response.status_code == 200
            record = response.json()
        record = held(client, record)
        response = confirm(client, record)
        assert response.status_code == (409 if started in {"journal", "head"} else 200), (
            response.text
        )


@pytest.mark.parametrize("phase", ["before_restore", "after_restore"])
def test_transfer_during_missing_byte_restore_cannot_confirm_for_later_owner(
    configuration, monkeypatch, phase
):
    from tests.integration.test_client_transfers import transfer_request

    from aether_agent_memory.runtime.contracts.client_transfers import TransferClientRun
    from aether_agent_memory.runtime.foundation.client_transfers import ClientTransfers

    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, _, payload, ref, before = recovery_object(
            service, client, monkeypatch, "definition"
        )
        foundation, objects = service.runtime.foundation, service.execution.inputs.objects
        original_get, original_put = objects.get_object_sync, objects.put_object_sync
        moved, restored, writes = None, False, []

        def transfer():
            nonlocal moved
            ctx = foundation.identity.context("alice")
            _, moved = ClientTransfers(ClientRuns(foundation.uow, foundation.identity)).transfer(
                ctx,
                UUID(record["run_id"]),
                "second-recovery",
                TransferClientRun.model_validate(transfer_request(record, "later-owner")),
            )

        def get(key):
            value = original_get(key)
            if value == payload and not restored:
                if phase == "before_restore" and moved is None:
                    transfer()
                return None
            return value

        def put(key, value):
            nonlocal restored
            writes.append(value)
            original_put(key, value)
            restored = True
            transfer()

        monkeypatch.setattr(objects, "get_object_sync", get)
        monkeypatch.setattr(objects, "put_object_sync", put)
        response = confirm(client, record, payload)
        assert response.status_code == 409 and moved is not None, response.text
        assert writes == ([] if phase == "before_restore" else [payload])
        with foundation.uow.transaction() as tx:
            assert tx.get(ref) == before
        assert client.get(
            f"/p3/client-runs/{record['run_id']}", headers=headers()
        ).json() == moved.record.model_dump(mode="json")


@pytest.mark.parametrize("fault", ["missing", "pending"])
def test_running_record_cannot_invent_missing_definition_confirmation(
    configuration, monkeypatch, fault
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = running(client)
        ordinary = f"/p3/client-runs/{record['run_id']}/definition"
        payload = client.get(ordinary, headers=headers()).content
        foundation = service.runtime.foundation
        ctx = foundation.identity.context("alice")
        ref = ClientDefinitions.ref(ctx, UUID(record["run_id"]))
        with foundation.uow.transaction() as tx:
            stored = tx.get(ref)
            if fault == "missing":
                tx.remove_if_revision(ref, stored["revision"])
            else:
                tx.put_if_revision(ref, {**stored, "state": "pending"}, stored["revision"])
        record = held(client, record)

        def forbidden(*args):
            pytest.fail("inconsistent definition history cannot be repaired through P2")

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", forbidden)
        response = confirm(client, record, payload)
        assert response.status_code == 400 and response.json()["code"] == "CONTRACT_VIOLATION", (
            response.text
        )


@pytest.mark.parametrize("fault", ["active", "transfer", "metadata"])
def test_confirmation_requires_intact_reservation_and_transfer(configuration, monkeypatch, fault):
    from aether_agent_memory.runtime.foundation.client_transfers import ClientTransfers

    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, _, _, ref, before = recovery_object(service, client, monkeypatch, "definition")
        foundation = service.runtime.foundation
        ctx = foundation.identity.context("alice")
        with foundation.uow.transaction() as tx:
            if fault == "metadata":
                tx.put_if_revision(ref, {**before, "state": "corrupt"}, before["revision"])
            elif fault == "transfer":
                tx.remove_if_revision(
                    ClientTransfers.ref(ctx, UUID(record["run_id"]), "recovery-1"), 1
                )
            else:
                index_ref = ClientRuns.ref(ctx, None)
                value = tx.get(index_ref)
                tx.put_if_revision(
                    index_ref,
                    {**value, "active": None, "revision": value["revision"] + 1},
                    value["revision"],
                )
        response = confirm(client, record)
        assert response.status_code == (409 if fault == "active" else 400), response.text


def test_confirmation_stream_enforces_ingress_limit_before_object_io(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, _, _, _, _ = recovery_object(service, client, monkeypatch, "definition")
        limit = service.runtime.remember.policy.max_input_bytes

        def forbidden(*args):
            pytest.fail("oversized definition must not reach P2")

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", forbidden)
        response = confirm(client, record, b"a" * (limit + 1))
        assert response.status_code == 400 and response.json()["code"] == "INVALID_ARGUMENT", (
            response.text
        )
