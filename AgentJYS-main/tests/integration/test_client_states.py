"""Versioned execution checkpoints are immutable P2 objects with a fenced head."""

import json
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_admission import admission_headers
from tests.integration.test_client_inputs import owner_headers
from tests.integration.test_client_run_registry import checkpoint, request
from tests.integration.test_client_scopes import prepare_operation, remember_body
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers

from azure_component_service import Service

pytestmark = pytest.mark.integration


def running(client):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    original = b'{"original":"fixed definition"}'
    response = client.post(
        path,
        headers=headers(),
        json={
            **request(run_id),
            "scope_policy": "p4_task_v1",
            "state_policy": "p4_state_v1",
            "definition": {
                "format_id": "p4_fixed_story_v1",
                "content_hash": sha256(original).hexdigest(),
                "size_bytes": len(original),
            },
        },
    )
    assert response.status_code == 201, response.text
    record = response.json()["record"]
    saved = client.put(path + "/definition", content=original, headers=owner_headers(record))
    assert saved.status_code == 200, saved.text
    started = client.put(
        path,
        headers=headers(),
        json=checkpoint(record, snapshot={**record["snapshot"], "state": "running"}),
    )
    assert started.status_code == 200, started.text
    return started.json()


def state(record, **changes):
    head = record.get("execution_state")
    return {
        "format_id": "p4_state_v1",
        "run_id": record["run_id"],
        "scenario_id": record["scenario_id"],
        "scope_id": record["scope_id"],
        "definition_hash": record["definition"]["content_hash"],
        "sequence": 1 if head is None else head["sequence"] + 1,
        "parent_hash": None if head is None else head["content_hash"],
        "operations": record["snapshot"].get("operations", []),
        "data": {"step": 1, "refs": {}, "receipts": {}, "phase": "before_effect"},
        **changes,
    }


def save(client, record, value):
    payload = json.dumps(value, ensure_ascii=False).encode()
    path = f"/p3/client-runs/{record['run_id']}/states/{value['sequence']}"
    return client.put(path, content=payload, headers=owner_headers(record)), payload, path


def test_state_head_and_original_bytes_survive_service_rebuild(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = running(client)
        value = state(record)
        stored, payload, path = save(client, record, value)
        assert stored.status_code == 200, stored.text
        updated = stored.json()
        assert updated["revision"] == record["revision"] + 1
        assert updated["execution_state"]["content_hash"] == sha256(payload).hexdigest()
        assert updated["execution_state"]["sequence"] == 1
        # Reconcile a lost reply against the original owner, revision and bytes.
        assert save(client, record, value)[0].json() == updated
    with TestClient(Service(configuration).app()) as client:
        restored = client.get(path, headers=headers())
        assert restored.status_code == 200 and restored.content == payload
        assert restored.headers["X-P3-State-Hash"] == sha256(payload).hexdigest()
        assert restored.headers["Cache-Control"] == "no-store"
        assert client.get(path, headers=headers(user="eve")).status_code == 404


@pytest.mark.parametrize(
    "change", ["owner", "revision", "scope", "definition", "sequence", "parent", "operations"]
)
def test_state_rejects_changed_parent_identity_and_operation_prefix(configuration, change):
    with TestClient(Service(configuration).app()) as client:
        record = running(client)
        first, _, _ = save(client, record, state(record))
        assert first.status_code == 200, first.text
        record = first.json()
        value = state(record)
        writer = dict(record)
        if change == "owner":
            writer["owner_id"] = "other"
        elif change == "revision":
            writer["revision"] -= 1
        elif change == "scope":
            value["scope_id"] = "other"
        elif change == "definition":
            value["definition_hash"] = "b" * 64
        elif change == "sequence":
            value["sequence"] += 1
        elif change == "parent":
            value["parent_hash"] = "b" * 64
        else:
            payload = json.dumps(remember_body(record)).encode()
            record = prepare_operation(client, record, payload, "/p3/remember")
            writer = record  # Correct revision, but state still lacks this original operation.
        refused, _, _ = save(client, writer, value)
        assert refused.status_code == 409, refused.text
        assert client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == record


@pytest.mark.parametrize("old_head", [False, True])
def test_new_state_policy_requires_current_checkpoint_before_business_admission(
    configuration, old_head
):
    with TestClient(Service(configuration).app()) as client:
        record = running(client)
        if old_head:
            first, _, _ = save(client, record, state(record))
            assert first.status_code == 200, first.text
            record = first.json()
        payload = json.dumps(remember_body(record)).encode()
        record = prepare_operation(client, record, payload, "/p3/remember")
        refused = client.post(
            "/p3/remember", content=payload, headers=admission_headers(record, "managed-op")
        )
        assert refused.status_code == 400 and refused.json()["code"] == "COMMIT_UNCONFIRMED", (
            refused.text
        )
        assert "X-P3-Job-ID" not in refused.headers
        saved, _, _ = save(client, record, state(record))
        assert saved.status_code == 200, saved.text
        record = saved.json()
        accepted = client.post(
            "/p3/remember", content=payload, headers=admission_headers(record, "managed-op")
        )
        assert "X-P3-Job-ID" in accepted.headers, accepted.text


def test_history_is_readable_but_cannot_replace_the_current_head(configuration):
    with TestClient(Service(configuration).app()) as client:
        original = running(client)
        first_value = state(original)
        first, first_bytes, first_path = save(client, original, first_value)
        assert first.status_code == 200, first.text
        second, second_bytes, second_path = save(client, first.json(), state(first.json()))
        assert second.status_code == 200, second.text
        assert second.json()["execution_state"]["parent_hash"] == sha256(first_bytes).hexdigest()
        assert client.get(first_path, headers=headers()).content == first_bytes
        assert client.get(second_path, headers=headers()).content == second_bytes
        assert save(client, original, first_value)[0].status_code == 409
        assert (
            client.get(first_path.rsplit("/states/", 1)[0], headers=headers()).json()
            == second.json()
        )


@pytest.mark.parametrize("fault", [None, b"corrupt"])
def test_ready_state_corruption_cannot_be_repaired_by_retry(configuration, monkeypatch, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = running(client)
        value = state(record)
        response, payload, path = save(client, record, value)
        assert response.status_code == 200, response.text
        objects = service.execution.inputs.objects
        original = objects.get_object_sync
        writes = []
        monkeypatch.setattr(
            objects,
            "get_object_sync",
            lambda key: fault if "/client-states/" in key else original(key),
        )
        monkeypatch.setattr(objects, "put_object_sync", lambda *args: writes.append(args))
        for result in (
            client.get(path, headers=headers()),
            client.put(path, content=payload, headers=owner_headers(record)),
        ):
            assert result.status_code == 400 and result.json()["code"] == "CONTRACT_VIOLATION", (
                result.text
            )
        assert not writes
        assert (
            client.get(path.rsplit("/states/", 1)[0], headers=headers()).json() == response.json()
        )


def test_state_head_commit_failure_preserves_pending_and_original_bytes(configuration, monkeypatch):
    from aether_agent_memory.runtime.contracts.models import ErrorCode
    from aether_agent_memory.runtime.foundation.common import FoundationError
    from aether_agent_memory.runtime.foundation.postgres import (
        PostgresTransaction as PostgresTransaction,
    )

    service = Service(configuration)
    original = PostgresTransaction.put_if_revision

    def fail_head(self, ref, value, expected_revision):
        if ref.object_type == "client_run" and value.get("execution_state"):
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "injected head failure")
        return original(self, ref, value, expected_revision)

    with TestClient(service.app()) as client:
        record = running(client)
        value = state(record)
        monkeypatch.setattr(PostgresTransaction, "put_if_revision", fail_head)
        rejected, payload, path = save(client, record, value)
        assert rejected.status_code == 503, rejected.text
        assert client.get(path.rsplit("/states/", 1)[0], headers=headers()).json() == record
        read = client.get(path, headers=headers())
        assert read.status_code == 400 and read.json()["code"] == "COMMIT_UNCONFIRMED"
        monkeypatch.setattr(PostgresTransaction, "put_if_revision", original)
        assert save(client, record, value)[0].status_code == 200
        assert client.get(path, headers=headers()).content == payload


@pytest.mark.parametrize("reading", [False, True])
def test_state_io_rechecks_current_identity(configuration, monkeypatch, reading):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = running(client)
        value = state(record)
        if reading:
            saved, _, path = save(client, record, value)
            assert saved.status_code == 200, saved.text
        original = service.execution.inputs.objects.get_object_sync

        def revoke(key):
            result = original(key)
            if "/client-states/" in key:
                with service.runtime.foundation.uow.transaction() as tx:
                    row = tx.read("identities", "alice")
                    tx.write("identities", "alice", {**row, "enabled": False})
            return result

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", revoke)
        result = client.get(path, headers=headers()) if reading else save(client, record, value)[0]
        assert result.status_code == 403 and result.json()["code"] == "FORBIDDEN", result.text


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("changed", ["owner", "revision", "active"])
def test_state_writer_fence_is_rechecked_after_io(configuration, monkeypatch, ready, changed):
    from uuid import UUID

    from aether_agent_memory.runtime.contracts.client_transfers import (
        TransferClientRun,
        client_run_hash,
    )
    from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
    from aether_agent_memory.runtime.foundation.client_transfers import ClientTransfers

    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = running(client)
        value = state(record)
        if ready:
            result, _, _ = save(client, record, value)
            assert result.status_code == 200, result.text
        foundation = service.runtime.foundation
        ctx = foundation.identity.context("alice")
        ref = ClientRuns.ref(ctx, None if changed == "active" else UUID(record["run_id"]))
        original = service.execution.inputs.objects.get_object_sync
        altered = False

        def race(key):
            nonlocal altered
            result = original(key)
            if "/client-states/" in key and not altered:
                if changed == "owner":
                    runs = ClientRuns(foundation.uow, foundation.identity)
                    current = runs.get(ctx, UUID(record["run_id"]))
                    ClientTransfers(runs).transfer(
                        ctx,
                        current.run_id,
                        "state-io-transfer",
                        TransferClientRun(
                            expected_owner_id=current.owner_id,
                            expected_revision=current.revision,
                            expected_record_hash=client_run_hash(current),
                            new_owner_id="replacement",
                        ),
                    )
                else:
                    with foundation.uow.transaction() as tx:
                        row = tx.get(ref)
                        update = {"active": None} if changed == "active" else {}
                        tx.put_if_revision(
                            ref, {**row, **update, "revision": row["revision"] + 1}, row["revision"]
                        )
                altered = True
            return result

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", race)
        result, _, path = save(client, record, value)
        assert result.status_code == 409, result.text
        current = client.get(path.rsplit("/states/", 1)[0], headers=headers()).json()
        assert current["snapshot"] == record["snapshot"]
        assert (current["execution_state"] is not None) == ready


@pytest.mark.parametrize("same", [False, True])
def test_concurrent_state_writes_publish_one_head(configuration, monkeypatch, same):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = running(client)
        value = state(record)
        entered, release = Event(), Event()
        original = service.execution.inputs.objects.get_object_sync

        def block_first(key):
            if "/client-states/" in key and not entered.is_set():
                entered.set()
                assert release.wait(20)
            return original(key)

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", block_first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(save, client, record, value)
            try:
                assert entered.wait(20)
                second_value = value if same else {**value, "data": {"step": 99}}
                second, _, _ = save(client, record, second_value)
                assert second.status_code == (200 if same else 409), second.text
            finally:
                release.set()
            result, payload, path = first.result(20)
        assert result.status_code == 200, result.text
        assert result.json()["revision"] == record["revision"] + 1
        assert client.get(path, headers=headers()).content == payload
        if same:
            assert result.json() == second.json()


@pytest.mark.parametrize(
    "difference", ["large", "malformed", "sequence", "credentials", "duplicate"]
)
def test_invalid_state_payload_never_reaches_p2(configuration, monkeypatch, difference):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = running(client)
        value = state(record)
        path = f"/p3/client-runs/{record['run_id']}/states/1"
        payload = json.dumps(value).encode()
        if difference == "large":
            payload = b" " * 262145
        elif difference == "malformed":
            payload = b"{"
        elif difference == "sequence":
            path = path[:-1] + "1025"
            payload = json.dumps({**value, "sequence": 1025}).encode()
        elif difference == "credentials":
            payload = json.dumps({**value, "data": {"nested": {"token": "secret"}}}).encode()
        else:
            payload = payload[:-1] + b', "sequence": 2}'

        def unexpected(*args):
            pytest.fail("invalid state cannot reach P2")

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", unexpected)
        monkeypatch.setattr(service.execution.inputs.objects, "put_object_sync", unexpected)
        result = client.put(path, content=payload, headers=owner_headers(record))
        assert result.status_code in {400, 422}, result.text
        assert client.get(path.rsplit("/states/", 1)[0], headers=headers()).json() == record


@pytest.mark.parametrize("missing", ["definition", "scope_policy"])
def test_state_policy_requires_original_definition_and_managed_scope(configuration, missing):
    with TestClient(Service(configuration).app()) as client:
        run_id = str(uuid4())
        spec = {
            **request(run_id),
            "scope_policy": "p4_task_v1",
            "state_policy": "p4_state_v1",
            "definition": {"format_id": "original", "content_hash": "a" * 64, "size_bytes": 1},
        }
        del spec[missing]
        result = client.post("/p3/client-runs/" + run_id, headers=headers(), json=spec)
        assert result.status_code == 422, result.text


def test_original_operation_order_cannot_be_changed(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = running(client)
        payload = json.dumps(remember_body(record)).encode()
        for operation in ("first", "second"):
            record = prepare_operation(client, record, payload, "/p3/remember", operation=operation)
        path = "/p3/client-runs/" + record["run_id"]
        result = client.put(
            path,
            headers=headers(),
            json=checkpoint(
                record,
                snapshot={
                    **record["snapshot"],
                    "operations": list(reversed(record["snapshot"]["operations"])),
                },
            ),
        )
        assert result.status_code == 409, result.text
        assert client.get(path, headers=headers()).json() == record


@pytest.mark.parametrize("fault", ["transport", "forbidden"])
def test_state_preserves_p2_errors_and_can_retry_original_pending_state(
    configuration, monkeypatch, fault
):
    from aether_agent_memory.runtime.contracts.models import ErrorCode
    from aether_agent_memory.runtime.foundation.common import FoundationError

    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = running(client)
        value = state(record)
        objects = service.execution.inputs.objects
        original = objects.get_object_sync

        def fail(key):
            if "/client-states/" in key:
                if fault == "forbidden":
                    raise FoundationError(ErrorCode.FORBIDDEN, "injected permission refusal")
                raise RuntimeError("injected network outage")
            return original(key)

        monkeypatch.setattr(objects, "get_object_sync", fail)
        refused, _, path = save(client, record, value)
        assert refused.status_code == (403 if fault == "forbidden" else 503), refused.text
        assert client.get(path.rsplit("/states/", 1)[0], headers=headers()).json() == record
        monkeypatch.setattr(objects, "get_object_sync", original)
        result, payload, _ = save(client, record, value)
        assert result.status_code == 200, result.text
        assert client.get(path, headers=headers()).content == payload


def test_p2_saved_bytes_with_lost_reply_are_confirmed_without_overwrite(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = running(client)
        value = state(record)
        objects = service.execution.inputs.objects
        original = objects.put_object_sync

        def lose_reply(key, payload):
            original(key, payload)
            raise RuntimeError("injected reply loss after P2 write")

        monkeypatch.setattr(objects, "put_object_sync", lose_reply)
        refused, payload, path = save(client, record, value)
        assert refused.status_code == 503, refused.text
        assert client.get(path.rsplit("/states/", 1)[0], headers=headers()).json() == record
        pending = client.get(path, headers=headers())
        assert pending.json()["code"] == "COMMIT_UNCONFIRMED"
        monkeypatch.setattr(
            objects,
            "put_object_sync",
            lambda *args: pytest.fail("cannot overwrite already saved original bytes"),
        )
        result, _, _ = save(client, record, value)
        assert result.status_code == 200, result.text
        assert client.get(path, headers=headers()).content == payload


def test_read_checks_original_state_against_published_head(configuration):
    from uuid import UUID

    from aether_agent_memory.runtime.foundation.client_runs import ClientRuns

    service = Service(configuration)
    with TestClient(service.app()) as client:
        record = running(client)
        saved, _, path = save(client, record, state(record))
        assert saved.status_code == 200, saved.text
        foundation = service.runtime.foundation
        ref = ClientRuns.ref(foundation.identity.context("alice"), UUID(record["run_id"]))
        with foundation.uow.transaction() as tx:
            row = tx.get(ref)
            changed = {
                **row,
                "execution_state": {**row["execution_state"], "content_hash": "b" * 64},
                "revision": row["revision"] + 1,
            }
            tx.put_if_revision(ref, changed, row["revision"])
        rejected = client.get(path, headers=headers())
        assert rejected.status_code == 400 and rejected.json()["code"] == "CONTRACT_VIOLATION", (
            rejected.text
        )


@pytest.mark.parametrize("closed", ["passed", "unconfirmed"])
def test_closed_run_state_is_readable_but_new_state_is_fenced(configuration, closed):
    with TestClient(Service(configuration).app()) as client:
        record = running(client)
        saved, payload, path = save(client, record, state(record))
        assert saved.status_code == 200, saved.text
        run_path = path.rsplit("/states/", 1)[0]
        record = client.put(
            run_path, headers=headers(), json=checkpoint(saved.json(), state=closed)
        ).json()
        assert record["snapshot"]["state"] == closed
        assert client.get(path, headers=headers()).content == payload
        assert save(client, record, state(record))[0].status_code == 409


def test_existing_state_policy_cannot_be_downgraded(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = running(client)
        path = "/p3/client-runs/" + record["run_id"]
        changed = {
            **request(record["run_id"]),
            "scope_policy": "p4_task_v1",
            "definition": record["definition"],
        }
        assert client.post(path, headers=headers(), json=changed).status_code == 409
        assert client.get(path, headers=headers()).json() == record


def test_legacy_run_cannot_receive_retroactive_execution_state(configuration):
    with TestClient(Service(configuration).app()) as client:
        run_id = str(uuid4())
        path = "/p3/client-runs/" + run_id
        record = client.post(path, headers=headers(), json=request(run_id)).json()["record"]
        value = {
            "format_id": "p4_state_v1",
            "run_id": run_id,
            "scenario_id": record["scenario_id"],
            "scope_id": record["scope_id"],
            "definition_hash": "a" * 64,
            "sequence": 1,
            "parent_hash": None,
            "operations": [],
            "data": {},
        }
        assert save(client, record, value)[0].status_code == 409
        assert client.get(path, headers=headers()).json() == record
