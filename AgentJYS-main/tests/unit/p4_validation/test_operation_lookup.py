"""A lookup may reveal an original job, never authorize another submission."""

import httpx
import pytest

from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


def found(**changes):
    return {
        "operation_id": "original-op",
        "kind": "remember.save",
        "state": "found",
        "job_id": "original-job",
        "task_state": "succeeded",
        "input_hash": "a" * 64,
        "workflow_id": "p3/deployment/remember.save/original-job",
        **changes,
    }


@pytest.mark.parametrize("changes", [{"operation_id": "different"}, {"kind": "recall.execute"}])
def test_client_rejects_a_lookup_bound_to_another_operation(changes):
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=found(**changes))),
    )
    try:
        with pytest.raises(ValidationError) as error:
            client.lookup_operation("original-op", "remember.save")
        assert error.value.code == "upstream_protocol_error"
    finally:
        client.close()


def test_client_keeps_unconfirmed_lookup_as_a_read_only_unknown_result():
    calls = []

    def transport(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(
            200,
            json={
                "operation_id": "original-op",
                "kind": "remember.save",
                "state": "unconfirmed",
            },
        )

    client = P3ValidationClient(
        "http://p3.test", "secret", transport=httpx.MockTransport(transport)
    )
    try:
        result = client.lookup_operation("original-op", "remember.save")
        assert result.state == "unconfirmed" and result.job_id is None
        assert calls == [("GET", "/p3/operation-requests/original-op", {"kind": "remember.save"})]
    finally:
        client.close()
