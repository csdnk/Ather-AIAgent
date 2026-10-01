"""Fixed-route request encoding, not mock business success."""

import hashlib
import json

import httpx
import pytest

from aether_agent_memory.remember.contracts.models import SourceRef
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError


def test_document_upload_sends_utf8_bytes_and_tracks_only_safe_metadata():
    text = "演示图书馆：借期 30 天。"
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "kind": "document",
                "provider_id": "local",
                "document_id": "demo_rules",
                "document_version": "1",
                "expected_hash": hashlib.sha256(text.encode()).hexdigest(),
            },
        )

    client = P3ValidationClient(
        "http://p3", "NEVER_EXPOSE", transport=httpx.MockTransport(upstream)
    )
    calls = []
    try:
        with client.observe_calls(calls.append):
            result = client.upload_document("demo_rules", text, "upload_1")
        assert result.document_id == "demo_rules"
        assert len(requests) == 1
        assert requests[0].content == text.encode()
        assert requests[0].url.params["version"] == "1"
        assert requests[0].headers["content-type"].startswith("text/plain")
        assert requests[0].headers["X-Operation-ID"] == "upload_1"
        assert calls[0].method == "PUT"
        assert calls[0].path == "/p3/documents/{document_id}"
        assert calls[0].status_code == 200
        assert "NEVER_EXPOSE" not in json.dumps([c.model_dump() for c in calls])
        assert "借期" not in json.dumps([c.model_dump() for c in calls], ensure_ascii=False)
    finally:
        client.close()


@pytest.mark.parametrize(
    "method,args",
    [
        ("task", ("task_1",)),
        ("task_progress", ("task_1",)),
        ("tasks", ()),
        ("traces", ()),
        ("logs", ("trace_1",)),
        ("runtime", ()),
        ("readyz", ()),
        ("periodic_status", ()),
        ("incidents", ()),
    ],
)
def test_observation_reads_use_remaining_budget_without_replaying(method, args):
    requests = []

    def unavailable(request):
        requests.append(request)
        raise httpx.ReadTimeout("private upstream details", request=request)

    client = P3ValidationClient("http://p3", "secret", transport=httpx.MockTransport(unavailable))
    try:
        with pytest.raises(ValidationError, match="等待 P3 响应超时"):
            getattr(client, method)(*args, timeout_seconds=0.25)
        assert len(requests) == 1
        assert requests[0].extensions["timeout"]["read"] == 0.25
    finally:
        client.close()


def test_source_slice_offsets_are_query_parameters_not_added_to_reference():
    source = SourceRef(
        source_id="source_test", source_version=1, content_hash="a" * 64, locator="test"
    )
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "source": source.model_dump(mode="json"),
                "content": "借期",
                "start_char": 3,
                "end_char": 5,
                "total_chars": 12,
                "is_complete": False,
            },
        )

    client = P3ValidationClient("http://p3", "secret", transport=httpx.MockTransport(upstream))
    try:
        result = client.source_range(source, 3, 5)
        assert result.content == "借期"
        assert requests[0].url.params == httpx.QueryParams({"start": 3, "end": 5})
        assert json.loads(requests[0].content) == source.model_dump(mode="json")
    finally:
        client.close()
