"""A multi-read observation has one deadline and cannot send writes."""

import httpx
import pytest

from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


def test_read_budget_shrinks_across_requests_and_rejects_late_response(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("aether_p4_simulator.validation.client.time.monotonic", lambda: now[0])
    timeouts = []

    def reply(request):
        timeouts.append(request.extensions["timeout"]["read"])
        now[0] += 3
        return httpx.Response(
            200, json={"operation_id": "op", "kind": "remember.save", "state": "unconfirmed"}
        )

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(reply))
    try:
        with pytest.raises(ValidationError) as error, client.read_budget(5):
            assert client.lookup_operation("op", "remember.save").state == "unconfirmed"
            client.lookup_operation("op", "remember.save")
        assert error.value.code == "reconciliation_timeout"
        assert timeouts == [5, 2]
        client.lookup_operation("op", "remember.save")
        assert timeouts[-1] == 60  # The observation deadline does not leak to another caller.
    finally:
        client.close()


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_invalid_total_budget_sends_nothing(seconds):
    calls = []
    client = P3ValidationClient(
        "http://p3.test", "secret", transport=httpx.MockTransport(lambda r: calls.append(r))
    )
    try:
        with pytest.raises(ValidationError) as error, client.read_budget(seconds):
            pytest.fail("invalid budget was admitted")
        assert error.value.code == "invalid_argument" and calls == []
    finally:
        client.close()


def test_read_budget_forbids_effects_before_transport():
    calls = []
    client = P3ValidationClient(
        "http://p3.test", "secret", transport=httpx.MockTransport(lambda r: calls.append(r))
    )
    try:
        with pytest.raises(ValidationError) as error, client.read_budget(5):
            client.reindex("m1", "op")
        assert error.value.code == "read_only_observation" and calls == []
    finally:
        client.close()
