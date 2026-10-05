"""Caller state transport must bind exact bytes and the original run revision."""

from hashlib import sha256
from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.client_states import ClientExecutionState
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


def original():
    record = ClientRunRecord(
        run_id=uuid4(),
        scenario_id="library-basic",
        scope_id="p4r_" + "a" * 32,
        owner_id="owner",
        auth_epoch=1,
        revision=3,
        updated_at="2026-10-03T00:00:00.000Z",
        definition={"format_id": "p4_fixed_story_v1", "content_hash": "b" * 64, "size_bytes": 50},
        scope_policy="p4_task_v1",
        state_policy="p4_state_v1",
        snapshot={"state": "running", "operations": []},
    )
    envelope = ClientExecutionState(
        format_id="p4_state_v1",
        run_id=record.run_id,
        scenario_id=record.scenario_id,
        scope_id=record.scope_id,
        definition_hash="b" * 64,
        sequence=1,
        parent_hash=None,
        operations=[],
        data={},
    )
    payload = envelope.model_dump_json().encode()
    saved = record.model_copy(update={"revision": 4, "execution_state": envelope.binding(payload)})
    return record, payload, saved


@pytest.mark.parametrize(
    "changed", ["owner_id", "scope_id", "auth_epoch", "revision", "snapshot", "head", "policy"]
)
def test_state_save_rejects_changed_returned_binding(changed):
    record, payload, saved = original()
    raw = saved.model_dump(mode="json")
    if changed == "head":
        raw["execution_state"]["content_hash"] = "c" * 64
    elif changed == "policy":
        raw["state_policy"] = None
    elif changed == "snapshot":
        raw["snapshot"] = {"state": "passed"}
    else:
        raw[changed] = 99 if changed in {"revision", "auth_epoch"} else "foreign"
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw)),
    )
    try:
        with pytest.raises(ValidationError):
            client.save_client_state(record, payload)
    finally:
        client.close()


@pytest.mark.parametrize("fault", ["bytes", "header", "size"])
def test_state_read_refuses_damaged_original_bytes(fault):
    _, payload, saved = original()
    content = payload if fault != "bytes" else b"{}"
    digest = sha256(payload).hexdigest() if fault != "header" else "c" * 64
    if fault == "size":
        content = payload + b" "
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=content, headers={"X-P3-State-Hash": digest}
            )
        ),
    )
    try:
        with pytest.raises(ValidationError):
            client.read_client_state(saved)
    finally:
        client.close()


def test_state_save_and_read_preserve_exact_bytes_and_revision():
    record, payload, saved = original()
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "PUT":
            assert request.content == payload
            assert request.headers["X-P3-Run-Owner"] == "owner"
            assert request.headers["X-P3-Run-Revision"] == "3"
            return httpx.Response(200, json=saved.model_dump(mode="json"))
        return httpx.Response(
            200, content=payload, headers={"X-P3-State-Hash": sha256(payload).hexdigest()}
        )

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(respond))
    try:
        confirmed = client.save_client_state(record, payload)
        assert confirmed == saved
        assert client.read_client_state(confirmed) == payload
        assert [(item.method, item.url.path) for item in requests] == [
            ("PUT", f"/p3/client-runs/{record.run_id}/states/1"),
            ("GET", f"/p3/client-runs/{record.run_id}/states/1"),
        ]
    finally:
        client.close()
