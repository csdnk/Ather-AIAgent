"""Immutable run definitions travel through current P2 before caller execution."""

from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_inputs import owner_headers
from tests.integration.test_client_run_registry import checkpoint, request
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from azure_component_service import Service

pytestmark = pytest.mark.integration


def register(client, payload=b'{"scenario":"original","future_input":"old text"}'):
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    spec = request(run_id)
    spec["definition"] = {
        "format_id": "p4_fixed_story_v1",
        "content_hash": sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    response = client.post(path, headers=headers(), json=spec)
    assert response.status_code == 201, response.text
    return response.json()["record"], path + "/definition", payload


def test_definition_must_be_ready_before_start_and_survives_service_rebuild(configuration):
    with TestClient(Service(configuration).app()) as client:
        record, path, payload = register(client)
        running = checkpoint(record, snapshot={**record["snapshot"], "state": "running"})
        rejected = client.put(path.removesuffix("/definition"), headers=headers(), json=running)
        assert rejected.status_code == 400 and rejected.json()["code"] == "COMMIT_UNCONFIRMED"
        saved = client.put(path, content=payload, headers=owner_headers(record))
        assert saved.status_code == 200, saved.text
        assert saved.json()["content_hash"] == sha256(payload).hexdigest()
        assert saved.json()["state"] == "ready"
        assert (
            client.put(path, content=payload, headers=owner_headers(record)).json() == saved.json()
        )
        started = client.put(path.removesuffix("/definition"), headers=headers(), json=running)
        assert started.status_code == 200, started.text
    with TestClient(Service(configuration).app()) as client:
        restored = client.get(path, headers=headers())
        assert restored.status_code == 200 and restored.content == payload
        assert restored.headers["X-P3-Definition-Hash"] == sha256(payload).hexdigest()
        assert client.get(path, headers=headers(user="eve")).status_code == 404


@pytest.mark.parametrize("difference", ["owner", "revision", "body"])
def test_changed_definition_is_rejected_before_p2_io(configuration, monkeypatch, difference):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, path, payload = register(client)

        def unexpected(*args):
            pytest.fail("invalid writer or bytes cannot reach P2")

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", unexpected)
        monkeypatch.setattr(service.execution.inputs.objects, "put_object_sync", unexpected)
        extra = (
            {"X-P3-Run-Owner": "foreign"}
            if difference == "owner"
            else ({"X-P3-Run-Revision": "999"} if difference == "revision" else {})
        )
        result = client.put(
            path,
            content=b"changed" if difference == "body" else payload,
            headers=owner_headers(record, **extra),
        )
        assert result.status_code == 409, result.text
        assert client.get(path.removesuffix("/definition"), headers=headers()).json() == record


def test_legacy_run_cannot_be_backfilled_with_current_definition(configuration):
    with TestClient(Service(configuration).app()) as client:
        run_id = str(uuid4())
        path = "/p3/client-runs/" + run_id
        registered = client.post(path, headers=headers(), json=request(run_id))
        record = registered.json()["record"]
        result = client.put(path + "/definition", content=b"new", headers=owner_headers(record))
        assert result.status_code == 409, result.text
        assert client.get(path, headers=headers()).json() == record


def test_definition_final_commit_failure_leaves_pending_and_prevents_execution(
    configuration, monkeypatch
):
    service = Service(configuration)
    original = PostgresTransaction.put_if_revision

    def reject(self, ref, value, expected_revision):
        if ref.object_type == "client_run_definition" and value["state"] == "ready":
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "injected definition commit failure"
            )
        return original(self, ref, value, expected_revision)

    with TestClient(service.app()) as client:
        record, path, payload = register(client)
        monkeypatch.setattr(PostgresTransaction, "put_if_revision", reject)
        response = client.put(path, content=payload, headers=owner_headers(record))
        assert response.status_code == 503, response.text
        assert client.get(path, headers=headers()).json()["code"] == "COMMIT_UNCONFIRMED"
        running = checkpoint(record, snapshot={**record["snapshot"], "state": "running"})
        assert (
            client.put(
                path.removesuffix("/definition"), headers=headers(), json=running
            ).status_code
            == 400
        )
        monkeypatch.setattr(PostgresTransaction, "put_if_revision", original)
        assert client.put(path, content=payload, headers=owner_headers(record)).status_code == 200
        assert client.get(path, headers=headers()).content == payload


@pytest.mark.parametrize("fault", [None, b"corrupt"])
def test_definition_corruption_is_not_repaired_by_ready_retry(configuration, monkeypatch, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, path, payload = register(client)
        assert client.put(path, content=payload, headers=owner_headers(record)).status_code == 200
        original = service.execution.inputs.objects.get_object_sync
        monkeypatch.setattr(
            service.execution.inputs.objects,
            "get_object_sync",
            lambda key: fault if key.startswith("runtime/client-definitions/") else original(key),
        )
        for response in (
            client.get(path, headers=headers()),
            client.put(path, content=payload, headers=owner_headers(record)),
        ):
            assert response.status_code == 400 and response.json()["code"] == "CONTRACT_VIOLATION"
        assert client.get(path.removesuffix("/definition"), headers=headers()).json() == record


@pytest.mark.parametrize("reading", [False, True])
def test_identity_revoked_during_definition_io_is_rejected(configuration, monkeypatch, reading):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, path, payload = register(client)
        if reading:
            assert (
                client.put(path, content=payload, headers=owner_headers(record)).status_code == 200
            )
        original = service.execution.inputs.objects.get_object_sync

        def revoke(key):
            result = original(key)
            if key.startswith("runtime/client-definitions/"):
                with service.runtime.foundation.uow.transaction() as tx:
                    row = tx.read("identities", "alice")
                    tx.write("identities", "alice", {**row, "enabled": False})
            return result

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", revoke)
        response = (
            client.get(path, headers=headers())
            if reading
            else client.put(path, content=payload, headers=owner_headers(record))
        )
        assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("ready", [False, True])
def test_original_owner_cannot_finish_definition_write_after_owner_changes(
    configuration, monkeypatch, ready
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, path, payload = register(client)
        if ready:
            assert (
                client.put(path, content=payload, headers=owner_headers(record)).status_code == 200
            )
        foundation = service.runtime.foundation
        ref = ClientRuns.ref(foundation.identity.context("alice"), UUID(record["run_id"]))
        original = service.execution.inputs.objects.get_object_sync
        changed = False

        def change_owner(key):
            nonlocal changed
            result = original(key)
            if key.startswith("runtime/client-definitions/") and not changed:
                with foundation.uow.transaction() as tx:
                    current = tx.get(ref)
                    tx.put_if_revision(
                        ref,
                        {**current, "owner_id": "replacement", "revision": current["revision"] + 1},
                        current["revision"],
                    )
                changed = True
            return result

        monkeypatch.setattr(service.execution.inputs.objects, "get_object_sync", change_owner)
        response = client.put(path, content=payload, headers=owner_headers(record))
        assert response.status_code == 409, response.text
        current = client.get(path.removesuffix("/definition"), headers=headers()).json()
        assert current["owner_id"] == "replacement" and current["snapshot"] == record["snapshot"]
        assert current["definition"] == record["definition"]
        read = client.get(path, headers=headers())
        assert read.status_code == (200 if ready else 400)


def test_same_run_registration_does_not_replace_original_definition(configuration):
    with TestClient(Service(configuration).app()) as client:
        record, path, payload = register(client)
        assert client.put(path, content=payload, headers=owner_headers(record)).status_code == 200
        candidate = request(record["run_id"])
        candidate["definition"] = {
            **record["definition"],
            "content_hash": sha256(b"new").hexdigest(),
            "size_bytes": 3,
        }
        response = client.post(path.removesuffix("/definition"), headers=headers(), json=candidate)
        assert response.status_code == 200 and response.json()["created"] is False
        assert response.json()["record"] == record
        assert client.put(path, content=b"new", headers=owner_headers(record)).status_code == 409
        assert client.get(path, headers=headers()).content == payload
