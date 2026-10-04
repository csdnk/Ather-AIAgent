"""P4 must bind transfer replies to its exact original intent and preserve uncertainty."""

from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from tests.unit.p4_demo.test_execution_state import owned

from aether_agent_memory.runtime.contracts.client_ownership import ClientOwnerEpoch, ClientOwnership
from aether_agent_memory.runtime.contracts.client_transfers import (
    ClientRunTransferResult,
    TransferClientRun,
    client_run_hash,
)
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


def records():
    before = owned()[-1]
    before = before.model_copy(
        update={
            "ownership": ClientOwnership(
                epochs=(ClientOwnerEpoch(owner_id=before.owner_id, first_revision=1),)
            )
        }
    )
    spec = TransferClientRun(
        expected_owner_id=before.owner_id,
        expected_revision=before.revision,
        expected_record_hash=client_run_hash(before),
        new_owner_id="next",
    )
    after = before.model_copy(
        update={
            "owner_id": "next",
            "revision": before.revision + 1,
            "ownership": ClientOwnership(
                epochs=(
                    *before.ownership.epochs,
                    ClientOwnerEpoch(
                        owner_id="next", first_revision=before.revision + 1, transfer_id="transfer"
                    ),
                ),
                recovery_transfer_id="transfer",
            ),
        }
    )
    result = ClientRunTransferResult(
        run_id=before.run_id, transfer_id="transfer", request=spec, previous=before, record=after
    )
    return before, result


@pytest.mark.parametrize("fault", [None, "run", "transfer", "new_owner", "snapshot", "history"])
def test_transfer_response_is_bound_to_original_record_and_intent(fault):
    before, result = records()
    raw = result.model_dump(mode="json")
    if fault == "run":
        raw["run_id"] = str(uuid4())
    elif fault == "transfer":
        raw["transfer_id"] = "other"
    elif fault == "new_owner":
        raw["request"]["new_owner_id"] = "foreign"
    elif fault == "snapshot":
        raw["record"]["snapshot"]["state"] = "running-with-fabricated-progress"
    elif fault == "history":
        raw["record"]["ownership"]["epochs"][1]["first_revision"] -= 1
    calls = []

    def reply(request):
        import json

        calls.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(201, json=raw)

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(reply))
    try:
        if fault:
            with pytest.raises(ValidationError) as error:
                client.transfer_client_run(before, new_owner_id="next", transfer_id="transfer")
            assert (
                error.value.code == "upstream_protocol_error"
                and error.value.write_outcome == "unconfirmed"
            )
        else:
            assert (
                client.transfer_client_run(before, new_owner_id="next", transfer_id="transfer")
                == result
            )
        assert calls == [
            (
                "POST",
                f"/p3/client-runs/{before.run_id}/transfers/transfer",
                result.request.model_dump(mode="json"),
            )
        ]
    finally:
        client.close()


@pytest.mark.parametrize("state", ["unconfirmed", "committed"])
def test_transfer_lookup_only_reads_original_receipt(state):
    before, result = records()
    response = {
        "run_id": str(before.run_id),
        "transfer_id": "transfer",
        "state": state,
        "result": result.model_dump(mode="json") if state == "committed" else None,
    }
    calls = []
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(
            lambda request: (
                calls.append((request.method, request.url.path))
                or httpx.Response(200, json=deepcopy(response))
            )
        ),
    )
    try:
        found = client.lookup_client_transfer(before.run_id, "transfer")
        assert found.state == state and found.result == (result if state == "committed" else None)
        assert calls == [("GET", f"/p3/client-runs/{before.run_id}/transfers/transfer")]
    finally:
        client.close()
