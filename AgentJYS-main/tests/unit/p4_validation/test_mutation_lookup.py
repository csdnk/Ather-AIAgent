"""Commit evidence is typed, bound and never causes a write on an unknown result."""

import httpx
import pytest

from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


@pytest.mark.parametrize("changes", [{"operation_id": "another"}, {"kind": "source.delete"}])
def test_mutation_lookup_rejects_foreign_binding(changes):
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "operation_id": "original",
                    "kind": "remember.delete",
                    "state": "unconfirmed",
                    **changes,
                },
            )
        ),
    )
    try:
        with pytest.raises(ValidationError):
            client.lookup_mutation("original", "remember.delete")
    finally:
        client.close()


def test_mutation_lookup_is_read_only_when_no_receipt_is_available():
    calls = []

    def transport(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(
            200,
            json={"operation_id": "original", "kind": "remember.delete", "state": "unconfirmed"},
        )

    client = P3ValidationClient(
        "http://p3.test", "secret", transport=httpx.MockTransport(transport)
    )
    try:
        result = client.lookup_mutation("original", "remember.delete")
        assert result.receipt is None
        assert calls == [("GET", "/p3/mutation-receipts/original", {"kind": "remember.delete"})]
    finally:
        client.close()
