"""Explicit activation preserves original uncertainty and isolates old state reservations."""

import json
from copy import deepcopy
from hashlib import sha256
from uuid import UUID

import pytest
import rfc8785
from fastapi.testclient import TestClient
from tests.integration.test_client_inputs import owner_headers
from tests.integration.test_client_run_registry import checkpoint
from tests.integration.test_client_states import running, save, state
from tests.integration.test_client_transfers import transfer_path, transfer_request
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers
from tests.integration.test_transfer_write_boundaries import original_object

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_states import ClientStates
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from azure_component_service import Service

pytestmark = pytest.mark.integration


def held(client, record, transfer_id="recovery-1", owner="recoverer"):
    response = client.post(
        transfer_path(record, transfer_id), json=transfer_request(record, owner), headers=headers()
    )
    assert response.status_code == 201, response.text
    return response.json()["record"]


def recovery_request(record, *, target="running", data=None):
    transfer_id = record["ownership"]["recovery_transfer_id"]
    head = record["execution_state"]
    value = (
        None
        if target == "queued"
        else state(
            record,
            stream_id=transfer_id,
            parent_stream_id=None if head is None else head.get("stream_id"),
            data=data or {"phase": "before_effect", "recovered": True},
        )
    )
    if value is not None and value["parent_stream_id"] is None:
        del value["parent_stream_id"]
    return {
        "expected_owner_id": record["owner_id"],
        "expected_revision": record["revision"],
        "expected_record_hash": fingerprint(record),
        "target_state": target,
        "execution_state": value,
    }


def recovery_path(record, transfer_id=None):
    transfer_id = transfer_id or record["ownership"]["recovery_transfer_id"]
    return f"/p3/client-runs/{record['run_id']}/recoveries/{transfer_id}"


@pytest.mark.parametrize("missing_bytes", [False, True])
def test_recovery_keeps_old_pending_state_and_reads_original_history(
    configuration, monkeypatch, missing_bytes
):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        original = running(client)
        first, first_payload, first_path = save(client, original, state(original))
        assert first.status_code == 200, first.text
        original = first.json()
        old_value = state(original, data={"original_pending": True})
        old_payload = json.dumps(old_value, ensure_ascii=False).encode()
        objects = service.execution.inputs.objects
        put = objects.put_object_sync

        def lose_write(key, payload):
            if not missing_bytes:
                put(key, payload)
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "injected interrupted write")

        with monkeypatch.context() as patch:
            patch.setattr(objects, "put_object_sync", lose_write)
            lost, _, old_path = save(client, original, old_value)
        assert lost.status_code == 503, lost.text
        foundation = service.runtime.foundation
        ctx = foundation.identity.context("alice")
        old_ref = ClientStates.ref(ctx, UUID(original["run_id"]), 2)
        with foundation.uow.transaction() as tx:
            old_metadata = deepcopy(tx.get(old_ref))
        old_key = ClientStates.key(old_ref, sha256(old_payload).hexdigest())
        assert objects.get_object_sync(old_key) == (None if missing_bytes else old_payload)
        moved = held(client, original)
        body = recovery_request(moved)
        path = recovery_path(moved)
        response = client.post(path, json=body, headers=headers())
        assert response.status_code == 201, response.text
        result = response.json()
        record = result["record"]
        assert result["previous"] == moved
        assert record["ownership"]["recovery_transfer_id"] is None
        assert record["snapshot"] == original["snapshot"]
        assert record["execution_state"]["sequence"] == 2
        assert record["execution_state"]["stream_id"] == "recovery-1"
        new_path = old_path + "?stream_id=recovery-1"
        read = client.get(new_path, headers=headers())
        assert read.status_code == 200 and read.content == rfc8785.dumps(body["execution_state"])
        assert client.get(first_path, headers=headers()).content == first_payload
        assert client.get(old_path, headers=headers()).json()["code"] == "COMMIT_UNCONFIRMED"
        with foundation.uow.transaction() as tx:
            assert tx.get(old_ref) == old_metadata
        assert objects.get_object_sync(old_key) == (None if missing_bytes else old_payload)
        assert client.post(path, json=body, headers=headers()).json() == result
        assert client.get(path, headers=headers()).json()["result"] == result
        next_value = state(record, stream_id="recovery-1", parent_stream_id="recovery-1")
        next_response, _, next_path = save(client, record, next_value)
        assert next_response.status_code == 200, next_response.text
        assert client.get(next_path + "?stream_id=recovery-1", headers=headers()).status_code == 200
        assert client.get(first_path, headers=headers()).content == first_payload
        assert client.get(new_path, headers=headers()).content == read.content


def test_unconfirmed_activation_preserves_original_error_journal_and_receipt(configuration):
    with TestClient(Service(configuration).app()) as client:
        original = running(client)
        saved, _, _ = save(client, original, state(original))
        assert saved.status_code == 200
        original = saved.json()
        stopped = client.put(
            f"/p3/client-runs/{original['run_id']}",
            headers=headers(),
            json=checkpoint(
                original,
                snapshot={
                    **original["snapshot"],
                    "state": "unconfirmed",
                    "error": {"code": "original_timeout"},
                },
            ),
        )
        assert stopped.status_code == 200
        moved = held(client, stopped.json())
        body, path = recovery_request(moved), recovery_path(moved)
        response = client.post(path, json=body, headers=headers())
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["record"]["snapshot"] == {**moved["snapshot"], "state": "running"}
        assert result["previous"]["snapshot"]["state"] == "unconfirmed"
        assert (
            client.post(
                path,
                json={
                    **body,
                    "execution_state": {**body["execution_state"], "data": {"changed": True}},
                },
                headers=headers(),
            ).status_code
            == 409
        )
        assert client.get(path, headers=headers()).json()["result"] == result


def test_queued_activation_can_finish_original_definition_without_admitting_business(configuration):
    with TestClient(Service(configuration).app()) as client:
        original, definition_path, payload = original_object(client, "definition")
        moved = held(client, original)
        body = recovery_request(moved, target="queued")
        response = client.post(recovery_path(moved), json=body, headers=headers())
        assert response.status_code == 201, response.text
        record = response.json()["record"]
        assert record["snapshot"] == original["snapshot"]
        assert record["execution_state"] is None
        assert (
            client.put(definition_path, content=payload, headers=owner_headers(record)).status_code
            == 200
        )
        assert client.get(definition_path, headers=headers()).content == payload
        assert (
            client.put(
                definition_path, content=payload, headers=owner_headers(original)
            ).status_code
            == 409
        )
