"""Request correlation is read-only and does not turn task failure into success."""

import httpx
import pytest

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_p4_simulator.validation.client import P3ValidationClient


@pytest.mark.parametrize(
    ("difference", "expected"),
    [
        ("none", "matched"),
        ("target", "mismatch"),
        ("body", "mismatch"),
        ("content_type", "mismatch"),
        ("job", "mismatch"),
        ("missing_server", "unconfirmed"),
        ("legacy", "unconfirmed"),
    ],
)
def test_confirmation_preserves_original_binding_and_only_reads(difference, expected):
    intent = ClientOperation.prepare(
        method="POST",
        path="/p3/remember/{memory_id}/correct",
        operation_id="original",
        request_hash="a" * 64,
        target="/gateway/p3/remember/m1/correct",
        content_type="application/json",
    )
    value = intent.model_dump(mode="json")
    if difference == "legacy":
        value.pop("binding")
    if difference == "job":
        value.update(phase="observed", status_code=200, job_id="another-job")
    intent = ClientOperation.model_validate(value)
    original = intent.model_dump(mode="json")
    response = {
        "operation_id": "original",
        "kind": "remember.correct",
        "state": "found",
        "job_id": "original-job",
        "task_state": "failed",
        "input_hash": "c" * 64,
        "workflow_id": "p3/deployment/remember.correct/original-job",
        "http_request": None
        if difference == "missing_server"
        else {
            "version": 1,
            "method": "POST",
            "route": "/p3/remember/{memory_id}/correct",
            "target": "/p3/remember/m2/correct"
            if difference == "target"
            else "/p3/remember/m1/correct",
            "body_hash": ("b" if difference == "body" else "a") * 64,
            "content_type": "text/plain" if difference == "content_type" else "application/json",
        },
    }
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json=response)

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    try:
        confirmation = client.confirm_operation(intent)
        assert confirmation.state == expected
        assert confirmation.lookup.job_id == "original-job"
        assert confirmation.lookup.task_state == "failed"
        assert intent.model_dump(mode="json") == original
        assert calls == [("GET", "/p3/operation-requests/original", {"kind": "remember.correct"})]
    finally:
        client.close()


def test_unsupported_control_route_remains_unknown_without_network_effects():
    calls = []
    client = P3ValidationClient(
        "http://p3.test",
        "secret",
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)),
    )
    intent = ClientOperation.prepare(
        method="POST",
        path="/p3/backups",
        operation_id="original",
        request_hash="a" * 64,
        target="/p3/backups",
        content_type="application/json",
    )
    try:
        confirmation = client.confirm_operation(intent)
        assert confirmation.state == "unconfirmed" and confirmation.lookup is None
        assert calls == []
    finally:
        client.close()


@pytest.mark.parametrize("query", ["", "?start=0&end=5"])
@pytest.mark.parametrize("server_evidence", [True, False])
def test_document_intent_without_version_cannot_match_original_admission(query, server_evidence):
    intent = ClientOperation.prepare(
        method="PUT",
        path="/p3/documents/{document_id}",
        operation_id="original",
        request_hash="a" * 64,
        target="/p3/documents/doc" + query,
        content_type="text/plain",
    )
    original = intent.model_dump(mode="json")
    response = {
        "operation_id": "original",
        "kind": "remember.document",
        "state": "found",
        "job_id": "original-job",
        "task_state": "failed",
        "input_hash": "c" * 64,
        "workflow_id": "p3/deployment/remember.document/original-job",
        "http_request": {
            "version": 1,
            "method": "PUT",
            "route": "/p3/documents/{document_id}",
            "target": "/p3/documents/doc?version=1",
            "body_hash": "a" * 64,
            "content_type": "text/plain",
        }
        if server_evidence
        else None,
    }
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json=response)

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(upstream))
    try:
        confirmation = client.confirm_operation(intent)
        assert confirmation.state == ("mismatch" if server_evidence else "unconfirmed")
        assert confirmation.lookup.job_id == "original-job"
        assert confirmation.lookup.task_state == "failed"
        assert intent.model_dump(mode="json") == original
        assert calls == [("GET", "/p3/operation-requests/original", {"kind": "remember.document"})]
    finally:
        client.close()
