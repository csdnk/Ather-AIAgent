"""Legacy hashes and historical reads remain valid across explicit stream changes."""

from hashlib import sha256
from uuid import UUID

import pytest
import rfc8785
from fastapi.testclient import TestClient
from tests.integration.test_client_recoveries import held, recovery_path, recovery_request
from tests.integration.test_client_states import running, save, state
from tests.integration.test_client_transfer_races import business
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers

from aether_agent_memory.runtime.contracts.client_states import ClientExecutionState
from aether_agent_memory.runtime.contracts.client_transfers import ClientRunTransferResult
from aether_agent_memory.runtime.foundation.client_states import ClientStates
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from azure_component_service import Service

pytestmark = pytest.mark.integration


def test_legacy_envelope_binding_and_transfer_hash_do_not_gain_null_fields(configuration):
    with TestClient(Service(configuration).app()) as client:
        record = running(client)
        raw = state(record)
        response, payload, path = save(client, record, raw)
        assert response.status_code == 200
        assert ClientExecutionState.from_bytes(payload).model_dump(mode="json") == raw
        binding = response.json()["execution_state"]
        assert binding == {
            "format_id": "p4_state_v1",
            "sequence": 1,
            "parent_hash": None,
            "content_hash": sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "definition_hash": raw["definition_hash"],
            "operations_hash": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        }
        before = response.json()
        record = held(client, before)
        found = client.get(
            f"/p3/client-runs/{record['run_id']}/transfers/recovery-1", headers=headers()
        ).json()["result"]
        assert found["request"]["expected_record_hash"] == sha256(rfc8785.dumps(before)).hexdigest()
        assert ClientRunTransferResult.model_validate(found).model_dump(mode="json") == found
        assert client.get(path, headers=headers()).content == payload


@pytest.mark.parametrize("fault", ["missing", "parent_hash", "unpublished_branch"])
def test_history_requires_actual_ancestor_across_streams(configuration, monkeypatch, fault):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        original = running(client)
        first, _, path = save(client, original, state(original))
        assert first.status_code == 200
        second, _, _ = save(client, first.json(), state(first.json()))
        assert second.status_code == 200
        record = held(client, second.json())
        activated = client.post(
            recovery_path(record), json=recovery_request(record), headers=headers()
        )
        assert activated.status_code == 201
        ctx = service.runtime.foundation.identity.context("alice")
        parent_ref = ClientStates.ref(ctx, UUID(record["run_id"]), 2)
        get = PostgresTransaction.get

        def damaged(tx, ref):
            value = get(tx, ref)
            if ref == parent_ref:
                if fault == "missing":
                    return None
                if fault == "parent_hash":
                    return {**value, "binding": {**value["binding"], "content_hash": "0" * 64}}
            if fault == "unpublished_branch" and ref == ClientStates.ref(
                ctx, UUID(record["run_id"]), 2, "foreign"
            ):
                original_parent = get(tx, parent_ref)
                return {
                    **original_parent,
                    "binding": {**original_parent["binding"], "stream_id": "foreign"},
                }
            return value

        if fault == "unpublished_branch":
            path = f"/p3/client-runs/{record['run_id']}/states/2?stream_id=foreign"
        monkeypatch.setattr(PostgresTransaction, "get", damaged)
        refused = client.get(path, headers=headers())
        assert refused.status_code == 400 and refused.json()["code"] == "CONTRACT_VIOLATION"


def test_activation_must_keep_current_journal_including_unobserved_tail(configuration):
    with TestClient(Service(configuration).app()) as client:
        original, _ = business(client)
        record = held(client, original)
        body = recovery_request(record)
        body["execution_state"]["operations"] = []
        refused = client.post(recovery_path(record), json=body, headers=headers())
        assert refused.status_code == 409
        assert client.get(f"/p3/client-runs/{record['run_id']}", headers=headers()).json() == record
