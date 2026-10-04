import httpx
import pytest

from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


def payload():
    return {
        "receipt": {
            "operation_id": "original",
            "kind": "remember.consolidate",
            "intent_hash": "a" * 64,
            "result_hash": fingerprint({"task_ids": []}),
            "result_basis": "response",
            "targets": [
                {
                    "owner": "remember",
                    "object_type": "scope",
                    "object_id": "scope",
                    "scope": {
                        "tenant_id": "t",
                        "application_id": "a",
                        "user_id": "u",
                        "agent_id": "g",
                    },
                }
            ],
            "task_ids": [],
            "committed_at": "2026-10-03T00:00:00.000Z",
        },
        "response": {"task_ids": []},
    }


@pytest.mark.parametrize("change", ["operation_id", "kind", "hash", "response", "basis"])
def test_mutation_result_rejects_mismatched_binding_or_response(change):
    raw = payload()
    if change == "operation_id":
        raw["receipt"]["operation_id"] = "other"
    elif change == "kind":
        raw["receipt"]["kind"] = "remember.delete"
    elif change == "hash":
        raw["receipt"]["result_hash"] = "b" * 64
    elif change == "basis":
        raw["receipt"]["result_basis"] = "metadata_without_content"
    else:
        raw["response"] = {"task_ids": ["forged"]}
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw)),
    )
    try:
        with pytest.raises(ValidationError):
            client.mutation_result("original", "remember.consolidate")
    finally:
        client.close()


def test_mutation_result_uses_only_original_read_endpoint():
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json=payload())

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(respond))
    try:
        assert client.mutation_result("original", "remember.consolidate").response == {
            "task_ids": []
        }
        assert calls == [
            ("GET", "/p3/mutation-receipts/original/result", {"kind": "remember.consolidate"})
        ]
    finally:
        client.close()
