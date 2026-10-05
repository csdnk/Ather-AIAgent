"""A stored input receipt must bind the actual bytes; failed storage prevents send."""

from hashlib import sha256
from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation, ClientRunRecord
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


@pytest.mark.parametrize(
    "field", ["run_id", "operation_id", "request_hash", "binding_digest", "size_bytes"]
)
def test_input_acknowledgement_cannot_change_original_binding(field):
    payload = b"exact\noriginal"
    run_id = uuid4()
    intent = ClientOperation.prepare(
        method="POST",
        path="/p3/remember",
        operation_id="original",
        request_hash=sha256(payload).hexdigest(),
        target="/p3/remember",
        content_type="application/json",
    )
    record = ClientRunRecord(
        run_id=run_id,
        scenario_id="library-basic",
        scope_id="scope",
        owner_id="owner",
        auth_epoch=1,
        revision=2,
        updated_at="2026-10-03T00:00:00.000Z",
        snapshot={},
    )
    result = {
        "run_id": str(run_id),
        "operation_id": "original",
        "request_hash": intent.request_hash,
        "binding_digest": intent.binding.digest,
        "size_bytes": len(payload),
        "state": "ready",
    }
    result[field] = {
        "run_id": str(uuid4()),
        "operation_id": "foreign",
        "request_hash": "f" * 64,
        "binding_digest": "f" * 64,
        "size_bytes": len(payload) + 1,
    }[field]
    calls = []

    def upstream(request):
        calls.append(request)
        assert request.content == payload
        assert request.headers["X-P3-Run-Owner"] == "owner"
        assert request.headers["X-P3-Run-Revision"] == "2"
        assert "X-Operation-ID" not in request.headers
        return httpx.Response(200, json=result)

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    try:
        with pytest.raises(ValidationError):
            client.save_client_input(record, intent, payload)
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("fail", [False, True])
def test_input_persistence_occurs_after_intent_and_before_business_transport(fail):
    actions = []
    payload = b"exact original bytes"

    def observe(intent):
        actions.append(intent.phase)
        assert intent.request_hash == sha256(payload).hexdigest()

    def preserve(intent, received):
        assert actions == ["prepared"] and received == payload
        actions.append("stored")
        if fail:
            raise ValidationError(503, "input_unconfirmed", "input storage unavailable")

    def upstream(request):
        assert actions == ["prepared", "stored"] and request.content == payload
        actions.append("sent")
        return httpx.Response(200, json={})

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    try:
        with client.observe_effects(observe), client.preserve_inputs(preserve):
            if fail:
                with pytest.raises(ValidationError):
                    client._send("POST", "/p3/remember", operation_id="original", content=payload)
            else:
                client._send("POST", "/p3/remember", operation_id="original", content=payload)
        assert actions == (
            ["prepared", "stored"] if fail else ["prepared", "stored", "sent", "observed"]
        )
    finally:
        client.close()
