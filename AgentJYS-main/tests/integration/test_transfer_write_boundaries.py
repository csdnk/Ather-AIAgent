"""A transfer during real object IO must revoke the old publisher, including retries."""

import json
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_inputs import owner_headers
from tests.integration.test_client_run_registry import checkpoint, request
from tests.integration.test_client_scopes import remember_body
from tests.integration.test_client_states import running
from tests.integration.test_client_transfers import transfer_path, transfer_request
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_agent_memory.runtime.contracts.client_transfers import TransferClientRun
from aether_agent_memory.runtime.foundation.client_runs import ClientRuns
from aether_agent_memory.runtime.foundation.client_transfers import ClientTransfers
from azure_component_service import Service

pytestmark = pytest.mark.integration


def original_object(client, kind):
    if kind == "definition":
        run_id = str(uuid4())
        payload = b'{"original":"managed definition"}'
        response = client.post(
            f"/p3/client-runs/{run_id}",
            json={
                **request(run_id),
                "scope_policy": "p4_task_v1",
                "state_policy": "p4_state_v1",
                "definition": {
                    "format_id": "p4_fixed_story_v1",
                    "content_hash": sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                },
            },
            headers=headers(),
        )
        assert response.status_code == 201, response.text
        return response.json()["record"], f"/p3/client-runs/{run_id}/definition", payload
    record = running(client)
    payload = json.dumps(remember_body(record)).encode()
    operation = ClientOperation.prepare(
        method="POST",
        path="/p3/remember",
        operation_id="original-input",
        request_hash=sha256(payload).hexdigest(),
        target="/p3/remember",
        content_type="application/json",
    )
    response = client.put(
        f"/p3/client-runs/{record['run_id']}",
        json=checkpoint(
            record,
            snapshot={**record["snapshot"], "operations": [operation.model_dump(mode="json")]},
        ),
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    return response.json(), f"/p3/client-runs/{record['run_id']}/inputs/original-input", payload


@pytest.mark.parametrize("kind", ["definition", "input"])
@pytest.mark.parametrize("ready", [False, True])
def test_transfer_during_object_io_revokes_old_publisher(configuration, monkeypatch, kind, ready):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        record, path, payload = original_object(client, kind)
        if ready:
            stored = client.put(path, content=payload, headers=owner_headers(record))
            assert stored.status_code == 200, stored.text
        foundation = service.runtime.foundation
        runs = ClientRuns(foundation.uow, foundation.identity)
        ctx = foundation.identity.context("alice")
        objects = service.execution.inputs.objects
        method = "get_object_sync" if ready else "put_object_sync"
        original = getattr(objects, method)
        marker = "/client-definitions/" if kind == "definition" else "/client-inputs/"
        moved = None

        def transfer_after_io(key, *args):
            nonlocal moved
            result = original(key, *args)
            if marker in key and moved is None:
                _, moved = ClientTransfers(runs).transfer(
                    ctx,
                    UUID(record["run_id"]),
                    "during-io",
                    TransferClientRun.model_validate(transfer_request(record)),
                )
            return result

        with monkeypatch.context() as patch:
            patch.setattr(objects, method, transfer_after_io)
            refused = client.put(path, content=payload, headers=owner_headers(record))
        assert moved is not None and refused.status_code == 409, refused.text
        current = moved.record.model_dump(mode="json")
        assert (
            client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == current
        )
        assert client.get(transfer_path(record, "during-io"), headers=headers()).json()[
            "result"
        ] == moved.model_dump(mode="json")
        read = client.get(path, headers=headers())
        retry = client.put(path, content=payload, headers=owner_headers(current))
        if ready:
            assert read.status_code == 200 and read.content == payload
            assert retry.status_code == 200 and retry.json() == stored.json()
        else:
            assert read.status_code == 400 and read.json()["code"] == "COMMIT_UNCONFIRMED"
            assert retry.status_code == 409
        assert (
            client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == current
        )
