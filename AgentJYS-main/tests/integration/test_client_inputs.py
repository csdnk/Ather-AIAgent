"""Exact pre-send bytes use current P2; caller state remains reference metadata."""

from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_run_registry import checkpoint, request
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_inputs import ClientInputs
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from azure_component_service import Service

pytestmark = pytest.mark.integration


def prepare(client, payload):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    registered = client.post(path, json=request(run_id), headers=headers())
    assert registered.status_code == 201, registered.text
    record = registered.json()["record"]
    operation = ClientOperation.prepare(
        method="POST",
        path="/p3/remember",
        operation_id="original-input",
        request_hash=sha256(payload).hexdigest(),
        target="/p3/remember",
        content_type="application/json",
    )
    saved = client.put(
        path,
        headers=headers(),
        json=checkpoint(
            record,
            snapshot={
                **record["snapshot"],
                "state": "running",
                "operations": [operation.model_dump(mode="json")],
            },
        ),
    )
    assert saved.status_code == 200, saved.text
    return saved.json(), operation, path + "/inputs/" + operation.operation_id


def owner_headers(record, **changes):
    return {
        **headers(),
        "X-P3-Run-Owner": record["owner_id"],
        "X-P3-Run-Revision": str(record["revision"]),
        **changes,
    }


@pytest.mark.parametrize("payload", [b'{\n  "text": "original"\n}', b"\x00\xff\noriginal", b""])
def test_original_bytes_survive_restart_without_admitting_business(configuration, payload):
    with TestClient(Service(configuration).app()) as client:
        record, operation, path = prepare(client, payload)
        response = client.put(path, content=payload, headers=owner_headers(record))
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt == {
            "run_id": record["run_id"],
            "operation_id": operation.operation_id,
            "request_hash": sha256(payload).hexdigest(),
            "binding_digest": operation.binding.digest,
            "size_bytes": len(payload),
            "state": "ready",
        }
        assert client.get(path, headers=headers()).content == payload
        assert client.put(path, content=payload, headers=owner_headers(record)).json() == receipt
        assert client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == record
        assert client.get(path, headers=headers(user="eve")).status_code == 404
        lookup = client.get(
            "/p3/operation-requests/original-input?kind=remember.save", headers=headers()
        )
        assert lookup.status_code == 200 and lookup.json()["state"] == "unconfirmed"
    with TestClient(Service(configuration).app()) as client:
        restored = client.get(path, headers=headers())
        assert restored.status_code == 200 and restored.content == payload
        assert restored.headers["x-p3-request-hash"] == operation.request_hash


@pytest.mark.parametrize("difference", ["owner", "revision", "body"])
def test_invalid_preparation_is_rejected_before_p2_write(configuration, monkeypatch, difference):
    service = Service(configuration)
    writes = []
    original = service.execution.inputs.objects.put_object_sync

    def put(key, body):
        writes.append(key)
        return original(key, body)

    with TestClient(service.app()) as client:
        record, _, path = prepare(client, b"original")
        monkeypatch.setattr(service.execution.inputs.objects, "put_object_sync", put)
        extra = (
            {"X-P3-Run-Owner": "foreign"}
            if difference == "owner"
            else ({"X-P3-Run-Revision": "999"} if difference == "revision" else {})
        )
        result = client.put(
            path,
            content=b"changed" if difference == "body" else b"original",
            headers=owner_headers(record, **extra),
        )
        assert result.status_code == 409, result.text
        assert writes == []
        assert client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == record


def test_p2_write_then_commit_failure_retains_pending_and_original_run(configuration, monkeypatch):
    service = Service(configuration)
    original = PostgresTransaction.put_if_revision

    def reject(self, ref, value, expected_revision):
        if ref.object_type == "client_request_input" and value["state"] == "ready":
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "injected input commit failure")
        return original(self, ref, value, expected_revision)

    with TestClient(service.app()) as client:
        record, _, path = prepare(client, b"original")
        monkeypatch.setattr(PostgresTransaction, "put_if_revision", reject)
        result = client.put(path, content=b"original", headers=owner_headers(record))
        assert result.status_code == 503, result.text
        unknown = client.get(path, headers=headers())
        assert unknown.status_code == 400 and unknown.json()["code"] == "COMMIT_UNCONFIRMED"
        assert client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == record
        monkeypatch.setattr(PostgresTransaction, "put_if_revision", original)
        assert (
            client.put(path, content=b"original", headers=owner_headers(record)).status_code == 200
        )
        assert client.get(path, headers=headers()).content == b"original"


@pytest.mark.parametrize("fault", [None, b"corrupt"])
def test_ready_receipt_retry_revalidates_original_p2_bytes(configuration, monkeypatch, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path = prepare(client, b"original")
        assert (
            client.put(path, content=b"original", headers=owner_headers(record)).status_code == 200
        )
        objects = service.execution.inputs.objects
        original = objects.get_object_sync
        monkeypatch.setattr(
            objects,
            "get_object_sync",
            lambda key: fault if key.startswith("runtime/client-inputs/") else original(key),
        )
        read = client.get(path, headers=headers())
        assert read.status_code == 400 and read.json()["code"] == "CONTRACT_VIOLATION"
        retry = client.put(path, content=b"original", headers=owner_headers(record))
        assert retry.status_code == 400 and retry.json()["code"] == "CONTRACT_VIOLATION"
        assert client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == record


@pytest.mark.parametrize("ready", [False, True])
def test_owner_change_during_p2_io_rejects_late_writer(configuration, monkeypatch, ready):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path = prepare(client, b"original")
        foundation = service.runtime.foundation
        ctx = foundation.identity.context("alice")
        from uuid import UUID

        ref = ClientRuns.ref(ctx, UUID(record["run_id"]))
        objects = service.execution.inputs.objects
        if ready:
            assert (
                client.put(path, content=b"original", headers=owner_headers(record)).status_code
                == 200
            )
        method = "get_object_sync" if ready else "put_object_sync"
        original = getattr(objects, method)
        replaced = False

        def replace_owner(*args):
            nonlocal replaced
            result = original(*args)
            if not replaced:
                with foundation.uow.transaction() as tx:
                    prior = tx.get(ref)
                    tx.put_if_revision(
                        ref,
                        {**prior, "owner_id": "replacement", "revision": prior["revision"] + 1},
                        prior["revision"],
                    )
                replaced = True
            return result

        monkeypatch.setattr(objects, method, replace_owner)
        response = client.put(path, content=b"original", headers=owner_headers(record))
        assert response.status_code == 409, response.text
        pending = client.get(path, headers=headers())
        if ready:
            assert pending.status_code == 200 and pending.content == b"original"
        else:
            assert pending.status_code == 400 and pending.json()["code"] == "COMMIT_UNCONFIRMED"
        current = client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json()
        assert current["snapshot"] == record["snapshot"]
        assert current["owner_id"] == "replacement"


def test_observed_old_request_cannot_be_backfilled(configuration):
    with TestClient(Service(configuration).app()) as client:
        record, operation, path = prepare(client, b"original")
        observed = {**operation.model_dump(mode="json"), "phase": "observed", "status_code": 200}
        changed = client.put(
            "/p3/client-runs/" + record["run_id"],
            headers=headers(),
            json=checkpoint(
                record,
                snapshot={**record["snapshot"], "operations": [observed]},
            ),
        )
        assert changed.status_code == 200, changed.text
        current = changed.json()
        response = client.put(path, content=b"original", headers=owner_headers(current))
        assert response.status_code == 409, response.text
        assert client.get(path, headers=headers()).status_code == 404
        assert (
            client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == current
        )


@pytest.mark.parametrize("reading", [False, True])
def test_identity_revoked_during_p2_call_cannot_complete_request(
    configuration, monkeypatch, reading
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path = prepare(client, b"original")
        if reading:
            assert (
                client.put(path, content=b"original", headers=owner_headers(record)).status_code
                == 200
            )
        objects = service.execution.inputs.objects
        original = objects.get_object_sync

        def revoke(key):
            result = original(key)
            if key.startswith("runtime/client-inputs/"):
                with service.runtime.foundation.uow.transaction() as tx:
                    row = tx.read("identities", "alice")
                    tx.write("identities", "alice", {**row, "enabled": False})
            return result

        monkeypatch.setattr(objects, "get_object_sync", revoke)
        response = (
            client.get(path, headers=headers())
            if reading
            else client.put(path, content=b"original", headers=owner_headers(record))
        )
        assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"


def test_input_read_preserves_p2_authorization_failure(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path = prepare(client, b"original")
        assert (
            client.put(path, content=b"original", headers=owner_headers(record)).status_code == 200
        )

        def refuse(key):
            raise FoundationError(ErrorCode.FORBIDDEN, "injected P2 authorization failure")

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", refuse)
        response = client.get(path, headers=headers())
        assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"


def test_legacy_unbound_input_cannot_be_read_or_backfilled(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path = prepare(client, b"original")
        foundation = service.runtime.foundation
        ctx = foundation.identity.context("alice")
        ref = ClientRuns.ref(ctx, UUID(record["run_id"]))
        with foundation.uow.transaction() as tx:
            raw = tx.get(ref)
            raw["snapshot"]["operations"][0]["binding"] = None
            tx.put_if_revision(ref, raw, raw["revision"])
        legacy = client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json()

        def unexpected(*args):
            pytest.fail("legacy binding must be checked before object IO")

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", unexpected)
        monkeypatch.setattr(service.execution.inputs.objects, "put_object_sync", unexpected)
        read = client.get(path, headers=headers())
        write = client.put(path, content=b"original", headers=owner_headers(legacy))
        assert read.status_code == write.status_code == 409
        assert client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == legacy


def test_input_http_size_limit_prevents_p2_io_and_metadata_creation(configuration, monkeypatch):
    service = Service(configuration)
    payload = b"x" * (service.runtime.remember.policy.max_input_bytes + 1)
    with TestClient(service.app()) as client:
        record, _, path = prepare(client, payload)

        def unexpected(*args):
            pytest.fail("oversized input must not reach object IO")

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", unexpected)
        monkeypatch.setattr(service.execution.inputs.objects, "put_object_sync", unexpected)
        response = client.put(path, content=payload, headers=owner_headers(record))
        assert response.status_code == 400 and response.json()["code"] == "INVALID_ARGUMENT"
        assert client.get(path, headers=headers()).status_code == 404
        assert client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == record


def test_missing_p2_provider_has_no_local_input_fallback(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, operation, path = prepare(client, b"original")
        foundation = service.runtime.foundation
        ctx = foundation.identity.context("alice")
        run_id = UUID(record["run_id"])
        inputs = ClientInputs(ClientRuns(foundation.uow, foundation.identity), None, 1024)
        with pytest.raises(FoundationError) as error:
            inputs.prepare(
                ctx,
                run_id,
                operation.operation_id,
                record["owner_id"],
                record["revision"],
                b"original",
            )
        assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
        assert client.get(path, headers=headers()).status_code == 404
        assert (
            client.put(path, content=b"original", headers=owner_headers(record)).status_code == 200
        )
        with pytest.raises(FoundationError) as error:
            inputs.read(ctx, run_id, operation.operation_id)
        assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
        assert client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == record


def test_concurrent_original_input_writers_keep_same_receipt_and_run(
    configuration, monkeypatch, tmp_path
):
    import json
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local

    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, _, path = prepare(client, b"original")
        objects = service.execution.inputs.objects
        original = objects.get_object_sync
        meeting, thread_state = Barrier(2), local()
        write = objects.put_object_sync
        failures = []

        def observed_write(key, payload):
            try:
                return write(key, payload)
            except Exception as error:
                failures.append({"type": type(error).__name__, "details": str(error)})
                raise

        def simultaneous(key):
            result = original(key)
            if key.startswith("runtime/client-inputs/") and not getattr(thread_state, "met", False):
                thread_state.met = True
                meeting.wait(timeout=10)
            return result

        monkeypatch.setattr(objects, "get_object_sync", simultaneous)
        monkeypatch.setattr(objects, "put_object_sync", observed_write)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(client.put, path, content=b"original", headers=owner_headers(record))
                for _ in range(2)
            ]
            responses = [future.result(timeout=20) for future in futures]
        monkeypatch.setattr(objects, "get_object_sync", original)
        result = client.get(path, headers=headers())
        current = client.get("/p3/client-runs/" + record["run_id"], headers=headers())
        (tmp_path / "concurrent-input-evidence.json").write_text(
            json.dumps(
                {
                    "run_id": record["run_id"],
                    "statuses": [response.status_code for response in responses],
                    "p2_write_errors": failures,
                    "read_status": result.status_code,
                    "read_hash": sha256(result.content).hexdigest(),
                    "expected_hash": sha256(b"original").hexdigest(),
                    "run_unchanged": current.json() == record,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        assert [response.status_code for response in responses] == [200, 200], {
            "responses": [response.json() for response in responses],
            "p2_write_errors": failures,
        }
        assert responses[0].json() == responses[1].json()
        monkeypatch.setattr(objects, "get_object_sync", original)
        assert client.get(path, headers=headers()).content == b"original"
        assert client.get("/p3/client-runs/" + record["run_id"], headers=headers()).json() == record
