"""The executor uses the post-checkpoint revision only after input confirmation."""

from uuid import UUID

import httpx
import pytest

from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


def test_business_admission_uses_revision_after_preservation_and_does_not_leak_to_reads():
    record = ClientRunRecord(
        run_id=UUID("11111111-1111-4111-8111-111111111111"),
        scenario_id="library-basic",
        scope_id="demo_scope",
        owner_id="owner-a",
        auth_epoch=1,
        revision=1,
        updated_at="2026-10-03T00:00:00.000Z",
        snapshot={},
    )
    requests = []

    def journal(operation):
        nonlocal record
        record = record.model_copy(update={"revision": record.revision + 1})

    def preserve(operation, payload):
        nonlocal record
        assert record.revision == 2 and payload == b"original bytes"
        record = record.model_copy(update={"revision": 3})

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={})

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    try:
        with (
            client.observe_effects(journal),
            client.preserve_inputs(preserve),
            client.bind_client_run(lambda: record),
        ):
            client._send("POST", "/p3/remember", operation_id="original", content=b"original bytes")
            client._send("GET", "/p3/readyz")
        assert requests[0].headers["X-P3-Run-ID"] == "11111111-1111-4111-8111-111111111111"
        assert requests[0].headers["X-P3-Run-Owner"] == "owner-a"
        assert requests[0].headers["X-P3-Run-Revision"] == "3"
        assert not any(key.startswith("x-p3-run-") for key in requests[1].headers)
        client._send("POST", "/p3/remember", operation_id="outside", content=b"outside")
        assert not any(key.startswith("x-p3-run-") for key in requests[2].headers)
    finally:
        client.close()


def test_managed_executor_cannot_send_an_effect_without_its_journal():
    requests = []
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={})
        ),
    )
    try:
        with (
            client.bind_client_run(lambda: pytest.fail("unprepared effect has no revision")),
            pytest.raises(ValidationError),
        ):
            client._send("POST", "/p3/remember", operation_id="original", content=b"bytes")
        assert requests == []
    finally:
        client.close()
