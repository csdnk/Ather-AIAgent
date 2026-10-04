"""Atomic caller-owner transfers keep history and fence both executors until recovery."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from tests.integration.test_client_admission import admission_headers
from tests.integration.test_client_run_registry import checkpoint, request
from tests.integration.test_client_scopes import prepare_operation, remember_body
from tests.integration.test_client_states import running, save, state
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from azure_component_service import Service

pytestmark = pytest.mark.integration


def prepared(client):
    original = running(client)
    saved, _, _ = save(client, original, state(original))
    assert saved.status_code == 200, saved.text
    return saved.json()


def transfer_request(record, new_owner="worker-b"):
    return {
        "expected_owner_id": record["owner_id"],
        "expected_revision": record["revision"],
        "expected_record_hash": fingerprint(record),
        "new_owner_id": new_owner,
    }


def transfer_path(record, transfer_id="transfer-1"):
    return f"/p3/client-runs/{record['run_id']}/transfers/{transfer_id}"


def test_transfer_receipt_preserves_original_snapshot_head_and_survives_restart(configuration):
    with TestClient(Service(configuration).app()) as client:
        original = prepared(client)
        path = transfer_path(original)
        body = transfer_request(original)
        created = client.post(path, json=body, headers=headers())
        assert created.status_code == 201, created.text
        result = created.json()
        record = result["record"]
        assert result["previous"] == original and result["request"] == body
        assert result["run_id"] == original["run_id"] and result["transfer_id"] == "transfer-1"
        assert record["owner_id"] == "worker-b" and record["revision"] == original["revision"] + 1
        assert (
            record["snapshot"] == original["snapshot"]
            and record["execution_state"] == original["execution_state"]
        )
        assert record["ownership"]["recovery_transfer_id"] == "transfer-1"
        assert record["ownership"]["epochs"] == [
            {"owner_id": "worker-a", "first_revision": 1, "transfer_id": None},
            {
                "owner_id": "worker-b",
                "first_revision": record["revision"],
                "transfer_id": "transfer-1",
            },
        ]
        repeated = client.post(path, json=body, headers=headers())
        assert repeated.status_code == 200 and repeated.json() == result
        changed = client.post(path, json={**body, "new_owner_id": "other"}, headers=headers())
        assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_CONFLICT"
        new_id = str(uuid4())
        assert (
            client.post(
                f"/p3/client-runs/{new_id}", json=request(new_id), headers=headers()
            ).status_code
            == 409
        )
    with TestClient(Service(configuration).app()) as client:
        found = client.get(path, headers=headers())
        assert found.status_code == 200
        assert found.json() == {
            "run_id": original["run_id"],
            "transfer_id": "transfer-1",
            "state": "committed",
            "result": result,
        }
        assert client.post(path, json=body, headers=headers()).json() == result
        assert (
            client.get(f"/p3/client-runs/{original['run_id']}", headers=headers()).json() == record
        )


@pytest.mark.parametrize("fault", ["owner", "revision", "hash", "same_owner"])
def test_transfer_rejects_changed_expectations_without_receipt_or_owner_change(
    configuration, fault
):
    with TestClient(Service(configuration).app()) as client:
        original = prepared(client)
        body = transfer_request(original)
        if fault == "owner":
            body["expected_owner_id"] = "wrong"
        elif fault == "revision":
            body["expected_revision"] -= 1
        elif fault == "hash":
            body["expected_record_hash"] = "f" * 64
        else:
            body["new_owner_id"] = original["owner_id"]
        path = transfer_path(original)
        refused = client.post(path, json=body, headers=headers())
        assert refused.status_code == 409, refused.text
        found = client.get(path, headers=headers()).json()
        assert found["state"] == "unconfirmed" and found["result"] is None
        assert (
            client.get(f"/p3/client-runs/{original['run_id']}", headers=headers()).json()
            == original
        )


@pytest.mark.parametrize("same_transfer", [False, True])
def test_competing_transfers_have_one_owner_and_idempotent_original_result(
    configuration, same_transfer
):
    with TestClient(Service(configuration).app()) as client, ThreadPoolExecutor(2) as pool:
        original = prepared(client)
        values = [("a", "worker-b"), ("a", "worker-b") if same_transfer else ("b", "worker-c")]
        replies = list(
            pool.map(
                lambda value: client.post(
                    transfer_path(original, value[0]),
                    json=transfer_request(original, value[1]),
                    headers=headers(),
                ),
                values,
            )
        )
        assert sorted(reply.status_code for reply in replies) == (
            [200, 201] if same_transfer else [201, 409]
        )
        result = next(reply.json() for reply in replies if reply.status_code == 201)
        current = client.get(f"/p3/client-runs/{original['run_id']}", headers=headers()).json()
        assert current == result["record"] and len(current["ownership"]["epochs"]) == 2
        if same_transfer:
            assert replies[0].json() == replies[1].json()
        else:
            loser = "b" if result["transfer_id"] == "a" else "a"
            assert (
                client.get(transfer_path(original, loser), headers=headers()).json()["state"]
                == "unconfirmed"
            )


def test_failed_receipt_commit_rolls_back_owner_history_and_hold(configuration, monkeypatch):
    with TestClient(Service(configuration).app()) as client:
        original = prepared(client)
        put = PostgresTransaction.put_if_revision

        def fail_receipt(tx, ref, value, expected_revision):
            if ref.object_type == "client_run_transfer":
                raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "injected receipt failure")
            return put(tx, ref, value, expected_revision)

        with monkeypatch.context() as patch:
            patch.setattr(PostgresTransaction, "put_if_revision", fail_receipt)
            failed = client.post(
                transfer_path(original), json=transfer_request(original), headers=headers()
            )
        assert failed.status_code == 503, failed.text
        assert (
            client.get(f"/p3/client-runs/{original['run_id']}", headers=headers()).json()
            == original
        )
        assert (
            client.get(transfer_path(original), headers=headers()).json()["state"] == "unconfirmed"
        )
        assert (
            client.post(
                transfer_path(original), json=transfer_request(original), headers=headers()
            ).status_code
            == 201
        )


def test_transfer_fences_old_owner_and_holds_new_progress_and_state_writes(configuration):
    with TestClient(Service(configuration).app()) as client:
        original = prepared(client)
        transferred = client.post(
            transfer_path(original), json=transfer_request(original), headers=headers()
        )
        assert transferred.status_code == 201, transferred.text
        current = transferred.json()["record"]
        path = f"/p3/client-runs/{original['run_id']}"
        assert client.put(path, json=checkpoint(original), headers=headers()).status_code == 409
        assert client.put(path, json=checkpoint(current), headers=headers()).status_code == 409
        assert save(client, original, state(original))[0].status_code == 409
        assert save(client, current, state(current))[0].status_code == 409
        assert client.get(path, headers=headers()).json() == current


def test_unknown_legacy_ownership_is_not_backfilled_by_transfer(configuration):
    with TestClient(Service(configuration).app()) as client:
        run_id = str(uuid4())
        original = client.post(
            f"/p3/client-runs/{run_id}", json=request(run_id), headers=headers()
        ).json()["record"]
        refused = client.post(
            transfer_path(original), json=transfer_request(original), headers=headers()
        )
        assert refused.status_code == 409, refused.text
        assert client.get(f"/p3/client-runs/{run_id}", headers=headers()).json() == original


def test_transfer_holds_prepared_business_for_both_old_and_new_owner(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = running(client)
        payload = json.dumps(remember_body(record)).encode()
        record = prepare_operation(client, record, payload, "/p3/remember")
        saved, _, _ = save(client, record, state(record))
        assert saved.status_code == 200
        original = saved.json()
        changed = client.post(
            transfer_path(original), json=transfer_request(original), headers=headers()
        )
        assert changed.status_code == 201, changed.text
        current = changed.json()["record"]
        for owner in (original, current):
            refused = client.post(
                "/p3/remember", content=payload, headers=admission_headers(owner, "managed-op")
            )
            assert refused.status_code == 409, refused.text
            assert "X-P3-Job-ID" not in refused.headers
        assert (
            client.get(
                "/p3/operation-requests/managed-op?kind=remember.save", headers=headers()
            ).json()["state"]
            == "unconfirmed"
        )
        assert (
            client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == current
        )


def test_unconfirmed_transfer_preserves_original_error_and_journal(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = prepared(client)
        stopped = client.put(
            f"/p3/client-runs/{record['run_id']}",
            json=checkpoint(
                record,
                snapshot={
                    **record["snapshot"],
                    "state": "unconfirmed",
                    "error": {"code": "upstream_timeout", "operation_id": "original"},
                },
            ),
            headers=headers(),
        )
        assert stopped.status_code == 200, stopped.text
        original = stopped.json()
        response = client.post(
            transfer_path(original), json=transfer_request(original), headers=headers()
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert (
            result["previous"] == original and result["record"]["snapshot"] == original["snapshot"]
        )
        assert result["record"]["snapshot"]["state"] == "unconfirmed"
        refused = client.put(
            f"/p3/client-runs/{record['run_id']}",
            json=checkpoint(result["record"], "running"),
            headers=headers(),
        )
        assert refused.status_code == 409


@pytest.mark.parametrize("user", [None, "eve"])
def test_transfer_cannot_cross_authentication_or_identity(configuration, user):
    with TestClient(Service(configuration).app()) as client:
        original = prepared(client)
        credentials = {} if user is None else headers(user=user)
        refused = client.post(
            transfer_path(original), json=transfer_request(original), headers=credentials
        )
        assert refused.status_code == (401 if user is None else 404)
        assert (
            client.get(f"/p3/client-runs/{original['run_id']}", headers=headers()).json()
            == original
        )


def test_read_permission_does_not_grant_transfer_permission(configuration):
    raw = yaml.safe_load(configuration.identity_file.read_text("utf-8"))
    for identity in raw["identities"]:
        identity["principal"]["permissions"].remove("maintenance:recover")
    configuration.identity_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with TestClient(Service(configuration).app()) as client:
        original = prepared(client)
        refused = client.post(
            transfer_path(original), json=transfer_request(original), headers=headers()
        )
        assert refused.status_code == 403, refused.text
        assert (
            client.get(transfer_path(original), headers=headers()).json()["state"] == "unconfirmed"
        )


def test_transfer_replay_and_lookup_revalidate_original_identity_epoch(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = prepared(client)
        body, path = transfer_request(record), transfer_path(record)
        assert client.post(path, json=body, headers=headers()).status_code == 201
        raw = yaml.safe_load(configuration.identity_file.read_text("utf-8"))
        raw["revision"] += 1
        for identity in raw["identities"]:
            identity["principal"]["auth_epoch"] += 1
        configuration.identity_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
        service.reload_identity()
        assert client.get(path, headers=headers()).status_code == 403
        assert client.post(path, json=body, headers=headers()).status_code == 403


def test_legacy_managed_record_cannot_acquire_invented_owner_history(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = prepared(client)
        foundation = service.runtime.foundation
        ref = ClientRuns.ref(foundation.identity.context("alice"), UUID(record["run_id"]))
        with foundation.uow.transaction() as tx:
            raw = tx.get(ref)
            raw.pop("ownership")  # A pre-upgrade persisted managed record.
            tx.put_if_revision(ref, {**raw, "revision": raw["revision"] + 1}, raw["revision"])
        legacy = client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json()
        assert legacy["ownership"] is None
        refused = client.post(
            transfer_path(legacy), json=transfer_request(legacy), headers=headers()
        )
        assert refused.status_code == 409
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == legacy


def test_older_transfer_lookup_never_rewinds_a_newer_owner(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = prepared(client)
        body = transfer_request(record)
        first = client.post(transfer_path(record), json=body, headers=headers())
        assert first.status_code == 201
        second = client.post(
            transfer_path(record, "transfer-2"),
            json=transfer_request(first.json()["record"], "worker-c"),
            headers=headers(),
        )
        assert second.status_code == 201, second.text
        replay = client.post(transfer_path(record), json=body, headers=headers())
        assert replay.status_code == 200 and replay.json() == first.json()
        assert (
            client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json()
            == second.json()["record"]
        )


def test_full_ownership_history_keeps_receipts_but_refuses_another_transfer(configuration):
    with TestClient(Service(configuration).app()) as client:
        original = prepared(client)
        record = original
        first = None
        for number in range(1, 16):
            response = client.post(
                transfer_path(record, f"transfer-{number}"),
                json=transfer_request(record, f"replacement-{number}"),
                headers=headers(),
            )
            assert response.status_code == 201, response.text
            first = response.json() if first is None else first
            record = response.json()["record"]
        assert len(record["ownership"]["epochs"]) == 16
        refused = client.post(
            transfer_path(record, "over-limit"),
            json=transfer_request(record, "one-too-many"),
            headers=headers(),
        )
        assert refused.status_code == 400 and refused.json()["code"] == "CAPACITY_EXCEEDED"
        assert (
            client.get(transfer_path(record, "over-limit"), headers=headers()).json()["state"]
            == "unconfirmed"
        )
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record
        replay = client.post(
            transfer_path(original),
            json=transfer_request(original, "replacement-1"),
            headers=headers(),
        )
        assert replay.status_code == 200 and replay.json() == first


@pytest.mark.parametrize("state_name", ["passed", "failed", "blocked"])
def test_closed_run_cannot_regain_active_slot_through_transfer(configuration, state_name):
    with TestClient(Service(configuration).app()) as client:
        original = prepared(client)
        path = f"/p3/client-runs/{original['run_id']}"
        closed = client.put(path, json=checkpoint(original, state_name), headers=headers())
        assert closed.status_code == 200, closed.text
        record = closed.json()
        refused = client.post(
            transfer_path(record), json=transfer_request(record), headers=headers()
        )
        assert refused.status_code == 409
        assert client.get(path, headers=headers()).json() == record
        assert client.get(transfer_path(record), headers=headers()).json()["state"] == "unconfirmed"
        new_id = str(uuid4())
        assert (
            client.post(
                f"/p3/client-runs/{new_id}", json=request(new_id), headers=headers()
            ).status_code
            == 201
        )


def test_transfer_requires_original_active_slot(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = prepared(client)
        foundation = service.runtime.foundation
        ref = ClientRuns.ref(foundation.identity.context("alice"), None)
        with foundation.uow.transaction() as tx:
            index = tx.get(ref)
            tx.put_if_revision(
                ref, {**index, "active": None, "revision": index["revision"] + 1}, index["revision"]
            )
        refused = client.post(
            transfer_path(record), json=transfer_request(record), headers=headers()
        )
        assert refused.status_code == 409
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record
        assert client.get(transfer_path(record), headers=headers()).json()["state"] == "unconfirmed"


@pytest.mark.parametrize("reused", ["worker-a", "worker-b"])
def test_transfer_cannot_reuse_any_previous_owner(configuration, reused):
    with TestClient(Service(configuration).app()) as client:
        original = prepared(client)
        moved = client.post(
            transfer_path(original), json=transfer_request(original), headers=headers()
        )
        assert moved.status_code == 201
        record = moved.json()["record"]
        path = transfer_path(record, "reuse-owner")
        refused = client.post(path, json=transfer_request(record, reused), headers=headers())
        assert refused.status_code == 409
        assert client.get(path, headers=headers()).json()["state"] == "unconfirmed"
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record
