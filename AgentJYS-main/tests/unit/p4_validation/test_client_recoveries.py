"""Recovery transport binds exact intent and original journal before exposing success."""

import json
from copy import deepcopy

import httpx
import pytest
from tests.unit.p4_validation.test_client_transfers import records

from aether_agent_memory.runtime.contracts.client_recoveries import PrepareClientRecovery
from aether_agent_memory.runtime.contracts.client_transfers import client_run_hash
from aether_p4_simulator.demo.state import ExecutionData
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


def activation():
    before = records()[1].record
    envelope = ExecutionData().envelope(before).model_copy(update={"stream_id": "transfer"})
    spec = PrepareClientRecovery(
        expected_owner_id=before.owner_id,
        expected_revision=before.revision,
        expected_record_hash=client_run_hash(before),
        target_state="running",
        execution_state=envelope,
    )
    after = before.model_copy(
        update={
            "revision": before.revision + 1,
            "ownership": before.ownership.model_copy(update={"recovery_transfer_id": None}),
            "execution_state": spec.intent().state,
        }
    )
    return (
        before,
        spec,
        {
            "run_id": str(before.run_id),
            "transfer_id": "transfer",
            "request": spec.intent().model_dump(mode="json"),
            "previous": before.model_dump(mode="json"),
            "record": after.model_dump(mode="json"),
        },
    )


@pytest.mark.parametrize("fault", [None, "intent", "transfer", "snapshot"])
def test_activation_response_must_match_original_submitted_intent(fault):
    before, spec, raw = activation()
    if fault == "intent":
        # Internally valid receipt, but for different original state bytes.
        raw["request"]["state"]["content_hash"] = "e" * 64
        raw["record"]["execution_state"]["content_hash"] = "e" * 64
    elif fault == "transfer":
        raw["transfer_id"] = "foreign"
    elif fault == "snapshot":
        raw["record"]["snapshot"]["error"] = {"invented": True}
    sent = []

    def reply(request):
        sent.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(201, json=deepcopy(raw))

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(reply))
    try:
        if fault:
            with pytest.raises(ValidationError) as error:
                client.activate_client_run(before, spec.execution_state)
            assert (
                error.value.code == "upstream_protocol_error"
                and error.value.write_outcome == "unconfirmed"
            )
        else:
            result = client.activate_client_run(before, spec.execution_state)
            assert result.previous == before and result.request == spec.intent()
        assert sent == [
            (
                "POST",
                f"/p3/client-runs/{before.run_id}/recoveries/transfer",
                spec.model_dump(mode="json"),
            )
        ]
    finally:
        client.close()


@pytest.mark.parametrize("fault", ["stream_id", "parent_stream_id"])
def test_state_save_refuses_wrong_stream_before_sending(fault):
    before, spec, _ = activation()
    active = before.model_copy(
        update={
            "ownership": before.ownership.model_copy(update={"recovery_transfer_id": None}),
            "execution_state": spec.intent().state,
        }
    )
    envelope = (
        ExecutionData()
        .envelope(active)
        .model_copy(update={"stream_id": "transfer", "parent_stream_id": "transfer"})
    )
    payload = envelope.model_copy(update={fault: "wrong"}).model_dump_json().encode()
    sent = []

    def reply(request):
        sent.append(request)
        return httpx.Response(503, json={"code": "DEPENDENCY_UNAVAILABLE"})

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(reply))
    try:
        with pytest.raises(ValidationError):
            client.save_client_state(active, payload)
        assert sent == []
    finally:
        client.close()
