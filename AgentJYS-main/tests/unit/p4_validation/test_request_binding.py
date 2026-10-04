"""The journal must distinguish requests with identical bodies and different targets."""

from hashlib import sha256

import httpx
import pytest
from pydantic import ValidationError

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_p4_simulator.validation.client import P3ValidationClient


@pytest.mark.parametrize("difference", ["memory", "version"])
def test_intent_binds_the_actual_request_target_before_transport(difference):
    intents, sent = [], []

    def upstream(request):
        assert intents and intents[-1].phase == "prepared"
        value = intents[-1].model_dump(mode="json")
        binding = value.get("binding")
        assert binding is not None, "route template and body hash do not bind the actual target"
        assert binding["target"] == request.url.raw_path.decode("ascii")
        assert binding["content_type"] == request.headers.get("content-type", "")
        assert value["request_hash"] == sha256(request.content).hexdigest()
        assert "NEVER_EXPOSE" not in str(value)
        sent.append((request.url.raw_path, binding["digest"]))
        return httpx.Response(200, json={"task_id": "original-task"})

    client = P3ValidationClient(
        "http://p3.test", "NEVER_EXPOSE", transport=httpx.MockTransport(upstream)
    )
    try:
        with client.observe_effects(intents.append):
            for value in ("one", "two"):
                if difference == "memory":
                    client.reprocess("memory_" + value, "original")
                else:
                    client._send(
                        "PUT",
                        "/p3/documents/doc",
                        operation_id="original",
                        content=b"same body",
                        params={"version": value},
                    )
        assert len(sent) == 2 and sent[0][1] != sent[1][1]
        assert intents[0].request_hash == intents[2].request_hash
        assert intents[0].path == intents[2].path
        assert intents[0].binding == intents[1].binding
    finally:
        client.close()


@pytest.mark.parametrize(
    "target",
    [
        "https://p3.test/p3/documents/doc?version=1",
        "/p3/documents/doc#",
        "/p3/documents/doc?access_token=SECRET",
        "/p3/remember?version=1",
    ],
)
def test_binding_rejects_non_request_targets_and_credential_queries(target):
    with pytest.raises(ValidationError):
        ClientOperation.prepare(
            method="PUT",
            path="/p3/documents/{document_id}",
            operation_id="original",
            request_hash="a" * 64,
            target=target,
            content_type="text/plain",
        )
