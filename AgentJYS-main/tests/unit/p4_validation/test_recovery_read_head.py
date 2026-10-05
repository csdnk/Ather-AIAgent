"""A ready label cannot contradict the caller's authoritative published head."""

import httpx
import pytest
from tests.unit.p4_validation.test_client_transfers import records

from aether_agent_memory.runtime.contracts.client_recovery_reads import ClientRecoveryObjectEvidence
from aether_agent_memory.runtime.contracts.client_transfers import client_run_hash
from aether_p4_simulator.demo.state import ExecutionData, encode_state
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


@pytest.mark.parametrize("fault", ["no_head", "above_head", "different_head"])
@pytest.mark.parametrize("object_state", ["pending", "ready"])
def test_recovered_ready_state_cannot_contradict_published_head(fault, object_state):
    record = records()[1].record
    first = ExecutionData().envelope(record)
    if fault != "no_head":
        record = record.model_copy(update={"execution_state": first.binding(encode_state(first))})
    envelope = (
        first.model_copy(update={"data": {"different": True}})
        if fault == "different_head"
        else ExecutionData().envelope(record)
    )
    payload = encode_state(envelope)
    evidence = ClientRecoveryObjectEvidence(
        run_id=record.run_id,
        transfer_id="transfer",
        record_hash=client_run_hash(record),
        kind="state",
        state=object_state,
        object_revision=1 if object_state == "pending" else 2,
        binding=envelope.binding(payload),
    )

    def reply(request):
        assert request.method == "GET"
        assert request.url.params["expected_record_hash"] == client_run_hash(record)
        return httpx.Response(
            200, content=payload, headers={"X-P3-Recovery-Object": evidence.model_dump_json()}
        )

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(reply))
    try:
        if object_state == "ready":
            with pytest.raises(ValidationError) as error:
                client.read_recovery_state(record, envelope.sequence, envelope.stream_id)
            assert error.value.code == "upstream_protocol_error"
        else:
            result = client.read_recovery_state(record, envelope.sequence, envelope.stream_id)
            assert result.payload == payload and result.evidence.state == "pending"
    finally:
        client.close()
