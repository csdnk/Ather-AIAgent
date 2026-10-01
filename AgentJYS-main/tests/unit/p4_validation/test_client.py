"""Exercise the real synchronous adapter with isolated HTTP responses, never a live P3."""

import json
import traceback
from contextlib import contextmanager

import httpx
import pytest
from pydantic import ValidationError as ModelError

from aether_p4_simulator.validation import models
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError as AppError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("job_id", ["job_1", None, "", "../other", "x" * 129])
def test_pending_command_preserves_only_a_validated_job(job_id):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        headers = {"Location": "https://untrusted.invalid/secret"}
        if job_id is not None:
            headers["X-P3-Job-ID"] = job_id
        return httpx.Response(
            400, json={"code": "REQUEST_IN_PROGRESS", "message": "secret"}, headers=headers
        )

    with client_for(handler) as client, pytest.raises(AppError) as raised:
        invoke(client, "remember")
    error = raised.value
    assert error.write_outcome == "unconfirmed"
    if job_id == "job_1":
        assert error.job_id == "job_1"
        assert error.code == "request_in_progress"
    else:
        assert error.code == "upstream_protocol_error"
    assert "secret" not in "".join(traceback.format_exception(error))
    assert seen == [("POST", "/p3/remember")]


NOW = "2026-09-30T00:00:00.000Z"
SCOPE = {
    "tenant_id": "tenant-test",
    "application_id": "app-test",
    "user_id": "user-test",
    "agent_id": "agent-test",
    "session_id": "s-1",
    "task_id": "run-1",
}
SELECTION = {key: value for key, value in SCOPE.items() if key != "tenant_id"}
REF = {"scope": SCOPE, "memory_id": "m-1", "version": 1}
SOURCE = {
    "source_id": "source-1",
    "source_version": 1,
    "content_hash": "a" * 64,
    "locator": "conversation:op-1",
}
SOURCE_INPUT = {
    "kind": "conversation",
    "external_id": "op-1",
    "external_version": "1",
    "occurred_at": NOW,
}
RECEIPT = {
    "operation_id": "op-1",
    "saved": True,
    "source": SOURCE,
    "memories": [REF, {**REF, "memory_id": "m-2"}],
    "task_ids": ["task-1"],
    "phase": "processing",
}
MEMORY = {
    "ref": REF,
    "revision": 2,
    "object_revision": 3,
    "kind": "working",
    "status": "active",
    "content": "我喜欢无糖咖啡",
    "content_hash": "b" * 64,
    "sources": [SOURCE],
    "projection_state": "not_required",
    "created_at": NOW,
}
PROCESSING = {
    "memory": REF,
    "state": "awaiting_consolidation",
    "state_basis": "latest_task_per_memory_and_kind",
    "projection_state": "pending",
    "memory_status": "active",
    "derived_memory_ids": [],
    "historical_failed_tasks": 0,
    "tasks": [
        {
            "task_id": "task-1",
            "kind": "projection",
            "state": None,
            "error_code": None,
            "result_ref": None,
        }
    ],
}
PACK = {
    "recall_id": "r-1",
    "scope": SCOPE,
    "outcome": "empty",
    "selected_sources": ["working"],
    "coverage": {"working": "complete", "long_term": "not_requested"},
    "groups": [],
    "rendered_context": "",
    "token_budget": 2000,
    "tokens_used": 0,
    "tokenizer_id": "lexical",
    "policy_version": "v1",
    "degradation_reasons": [],
    "committed_at": NOW,
}
RECORD_REF = {"owner": "remember", "object_type": "memory", "object_id": "m-1", "scope": SCOPE}
TASK = {
    "task_id": "task-1",
    "owner_flow": "remember",
    "kind": "projection",
    "subject": RECORD_REF,
    "input_ref": RECORD_REF,
    "idempotency_key": "op-1",
    "input_hash": "c" * 64,
    "initiator_id": "actor-1",
    "initiator_auth_epoch": 1,
    "deadline_at": "2026-10-01T00:00:00.000Z",
    "state": "pending",
    "revision": 1,
    "attempt": 0,
    "effect_status": "not_started",
}
HEALTH = {
    "checked_at": NOW,
    "config_version": "v1",
    "liveness": "alive",
    "readiness": "not_ready",
    "required_capabilities": ["recall"],
    "observations": [],
}
CAPABILITIES = {
    "profile": "local",
    "embedding": "lexical",
    "semantic_processing": "literal_baseline",
    "object_storage": "local_sqlite",
    "scheduling": "continuous_heat_v1",
    "executor": "local",
    "operations": ["remember", "recall"],
}
LIVE = {"liveness": "alive", "checked_at": NOW}


@contextmanager
def client_for(handler):
    client = P3ValidationClient(
        "http://p3.test", "unit-test-only", transport=httpx.MockTransport(handler)
    )
    try:
        yield client
    finally:
        client.close()


def invoke(client, method):
    if method == "remember":
        return client.remember(
            models.RememberRequest(
                source=SOURCE_INPUT,
                selection=SELECTION,
                content={"kind": "text", "text": "  原文😀  "},
            ),
            "op-1",
        )
    if method == "recall":
        return client.recall(
            models.RecallRequest(
                query="喜欢什么？", selection=SELECTION, sources="working", token_budget=2000
            ),
            "op-1",
        )
    if method == "consolidate":
        return client.consolidate(models.ScopeSelector(**SELECTION), "op-1")
    if method == "correct":
        return client.correct(
            "m-1",
            models.CorrectionRequest(
                expected_version=1,
                expected_object_revision=3,
                content="红茶",
                source=SOURCE_INPUT,
                reason="用户确认",
            ),
            "op-1",
        )
    return getattr(client, method)(
        {"memory": "m-1", "processing": "m-1", "recall_result": "r-1", "task": "task-1"}[method]
    )


ROUTES = [
    ("remember", "POST", "/p3/remember", RECEIPT, models.RememberReceipt),
    ("recall", "POST", "/p3/recall", PACK, models.ContextPack),
    ("consolidate", "POST", "/p3/remember/consolidate", {"task_ids": []}, models.ConsolidateData),
    ("memory", "GET", "/p3/remember/m-1", MEMORY, models.MemorySnapshot),
    ("processing", "GET", "/p3/remember/m-1/processing", PROCESSING, models.ProcessingData),
    ("correct", "POST", "/p3/remember/m-1/correct", RECEIPT, models.RememberReceipt),
    ("recall_result", "GET", "/p3/recalls/r-1/result", PACK, models.ContextPack),
    ("task", "GET", "/p3/tasks/task-1", TASK, models.TaskRecord),
]


@pytest.mark.parametrize("method,verb,path,payload,result_type", ROUTES)
def test_fixed_routes_authentication_serialization_and_typed_responses(
    method,
    verb,
    path,
    payload,
    result_type,
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    with client_for(handler) as client:
        result = invoke(client, method)
    assert isinstance(result, result_type)
    assert len(requests) == 1
    request = requests[0]
    assert (request.method, request.url.path, request.url.host) == (verb, path, "p3.test")
    assert request.headers["Authorization"] == "Bearer unit-test-only"
    assert request.url.query == b""
    assert "traceparent" not in request.headers
    if verb == "GET":
        assert not request.content
        assert "X-Operation-ID" not in request.headers
    else:
        assert request.headers["X-Operation-ID"] == "op-1"
        body = json.loads(request.content)
        if method == "consolidate":
            assert body == SELECTION  # no selection wrapper and no tenant grant
        elif method == "remember":
            assert body["content"] == {"kind": "text", "text": "  原文😀  "}
            assert body["source"] == SOURCE_INPUT
            assert body["selection"] == SELECTION
        elif method == "correct":
            assert body == {
                "expected_version": 1,
                "expected_object_revision": 3,
                "content": "红茶",
                "source": SOURCE_INPUT,
                "reason": "用户确认",
            }
        else:
            assert body == {
                "query": "喜欢什么？",
                "selection": SELECTION,
                "sources": "working",
                "token_budget": 2000,
            }
    if method in {"remember", "correct"}:
        assert [ref.memory_id for ref in result.memories] == ["m-1", "m-2"]


@pytest.mark.parametrize(
    "status,code,want",
    [
        (400, "INVALID_ARGUMENT", "invalid_argument"),
        (401, "UNAUTHENTICATED", "unauthenticated"),
        (403, "FORBIDDEN", "forbidden"),
        (404, "NOT_FOUND", "not_found"),
        (409, "VERSION_CONFLICT", "version_conflict"),
        (409, "IDEMPOTENCY_CONFLICT", "idempotency_conflict"),
        (410, "RESULT_INVALIDATED", "recall_invalidated"),
        (429, "evil-code-with-secret", "upstream_rate_limited"),
        (500, "evil-code-with-secret", "upstream_error"),
        (503, "DEPENDENCY_UNAVAILABLE", "dependency_unavailable"),
        (504, "DEADLINE_EXCEEDED", "upstream_timeout"),
    ],
)
def test_http_errors_keep_status_but_never_leak_upstream_data(status, code, want):
    def handler(request):
        return httpx.Response(
            status,
            json={
                "code": code,
                "message": "must-not-appear",
                "secret": "unit-test-only",
                "operation_id": "forged",
            },
        )

    with client_for(handler) as client, pytest.raises(AppError) as raised:
        client.recall_result("r-1")
    error = raised.value
    assert (error.status, error.code, error.operation_id) == (status, want, None)
    assert error.write_outcome == "not_applicable"
    visible = str(error) + repr(error) + json.dumps(error.to_dict())
    assert not any(
        secret in visible
        for secret in [
            "must-not-appear",
            "unit-test-only",
            "forged",
            "evil-code-with-secret",
            "http://p3.test",
        ]
    )


@pytest.mark.parametrize(
    "payload",
    [None, [], {"code": []}, {"error": "expired"}, {"code": "FORBIDDEN", "message": "secret"}],
)
def test_410_requires_exact_business_code_before_it_can_prove_invalidation(payload):
    with (
        client_for(lambda request: httpx.Response(410, json=payload)) as client,
        pytest.raises(AppError) as raised,
    ):
        client.recall_result("r-1")
    assert (raised.value.status, raised.value.code) == (410, "upstream_protocol_error")


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"<html>secret</html>",
        b"null",
        b"[]",
        b"{}",
        b'{"recall_id":true}',
        b"{broken",
        b'"secret"',
    ],
)
def test_malformed_success_is_protocol_error_not_fallback(content):
    with (
        client_for(lambda request: httpx.Response(200, content=content)) as client,
        pytest.raises(AppError) as raised,
    ):
        client.recall_result("r-1")
    assert (raised.value.status, raised.value.code) == (502, "upstream_protocol_error")
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("method,verb,path,payload,result_type", ROUTES)
def test_every_endpoint_validates_response_shape(method, verb, path, payload, result_type):
    with (
        client_for(lambda request: httpx.Response(200, json={})) as client,
        pytest.raises(AppError) as raised,
    ):
        invoke(client, method)
    assert (raised.value.status, raised.value.code) == (502, "upstream_protocol_error")


@pytest.mark.parametrize("method", ["remember", "correct", "consolidate", "recall", "memory"])
@pytest.mark.parametrize(
    "error_type,status,code",
    [
        (httpx.ConnectError, 502, "upstream_unavailable"),
        (httpx.ReadTimeout, 504, "upstream_timeout"),
        (httpx.WriteTimeout, 504, "upstream_timeout"),
        (httpx.RemoteProtocolError, 502, "upstream_protocol_error"),
    ],
)
def test_transport_errors_are_safe_and_writes_have_no_automatic_retries(
    method,
    error_type,
    status,
    code,
):
    requests = []

    def handler(request):
        requests.append(request)
        raise error_type("unit-test-only http://p3.test must-not-appear", request=request)

    with client_for(handler) as client, pytest.raises(AppError) as raised:
        invoke(client, method)
    error = raised.value
    assert (error.status, error.code) == (status, code)
    assert len(requests) == 1
    if method in {"remember", "correct", "consolidate"} and error_type in {
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.RemoteProtocolError,
    }:
        assert error.write_outcome == "unconfirmed"
    if method in {"memory", "recall"}:
        assert error.write_outcome == "not_applicable"
    assert error.operation_id == (None if method == "memory" else "op-1")
    assert "must-not-appear" not in "".join(traceback.format_exception(error))


@pytest.mark.parametrize("status", [500, 504])
def test_write_http_failure_never_claims_definite_non_write(status):
    with (
        client_for(lambda request: httpx.Response(status, json={})) as client,
        pytest.raises(AppError) as raised,
    ):
        invoke(client, "remember")
    assert raised.value.write_outcome == "unconfirmed"


@pytest.mark.parametrize("method", ["memory", "processing", "recall_result", "task", "correct"])
@pytest.mark.parametrize("identifier", ["../other", "x/y", "x?y", "x#z", "x%2fy", "", "x" * 129])
def test_dynamic_ids_are_rejected_before_any_http_request(method, identifier):
    def handler(request):
        pytest.fail("invalid route ID reached transport")

    with client_for(handler) as client, pytest.raises(AppError) as raised:
        if method == "correct":
            client.correct(
                identifier,
                models.CorrectionRequest(
                    expected_version=1,
                    expected_object_revision=3,
                    content="红茶",
                    source=SOURCE_INPUT,
                    reason="确认",
                ),
                "op-1",
            )
        else:
            getattr(client, method)(identifier)
    assert raised.value.status == 400
    assert identifier not in str(raised.value) or identifier == ""


def test_invalid_operation_header_is_rejected_before_http():
    with (
        client_for(lambda request: pytest.fail("invalid operation ID reached transport")) as client,
        pytest.raises(AppError) as raised,
    ):
        client.consolidate(models.ScopeSelector(), "op\r\nAuthorization: injected")
    assert raised.value.status == 400
    assert raised.value.operation_id is None


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_never_followed_with_credentials(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "http://other.test/steal"})

    with client_for(handler) as client, pytest.raises(AppError) as raised:
        client.memory("m-1")
    assert raised.value.status == 502
    assert len(requests) == 1
    assert requests[0].url.host == "p3.test"


def test_null_selector_fields_are_omitted_and_client_is_reused_until_close():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"task_ids": []})

    with client_for(handler) as client:
        client.consolidate(models.ScopeSelector(task_id="run-1"), "op-1")
        client.consolidate(models.ScopeSelector(task_id="run-1"), "op-2")
        assert not client._http.is_closed
    assert client._http.is_closed
    assert [json.loads(request.content) for request in requests] == [{"task_id": "run-1"}] * 2
    assert [request.headers["X-Operation-ID"] for request in requests] == ["op-1", "op-2"]


def test_client_does_not_read_environment_proxy_or_ca_paths(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "not-a-valid-proxy")
    monkeypatch.setenv("HTTPS_PROXY", "not-a-valid-proxy")
    monkeypatch.setenv("SSL_CERT_FILE", "X:/nonexistent-unit-test-cert.pem")
    client = P3ValidationClient("https://p3.test", "unit-test-only")
    client.close()  # no request; construction must not consult env proxies/certificates


def test_status_probes_preserve_independent_results_timeouts_and_safe_data():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/p3/live":
            return httpx.Response(200, json=LIVE)
        if request.url.path == "/p3/health":
            raise httpx.ConnectError("must-not-appear", request=request)
        if request.url.path == "/p3/ready":
            return httpx.Response(403, json={"message": "must-not-appear"})
        return httpx.Response(200, json={**CAPABILITIES, "secret": "must-not-appear"})

    with client_for(handler) as client:
        status = client.status()
    assert status.live.ok and status.live.status_code == 200
    assert status.live.data.liveness == "alive"
    assert not status.health.ok and status.health.status_code is None
    assert status.health.error.code == "upstream_unavailable"
    assert not status.ready.ok and status.ready.status_code == 403
    assert status.ready.error.code == "forbidden"
    assert status.capabilities.data.semantic_processing == "literal_baseline"
    assert "must-not-appear" not in status.model_dump_json()
    assert [r.url.path for r in requests] == [
        "/p3/live",
        "/p3/health",
        "/p3/ready",
        "/p3/capabilities",
    ]
    assert all("X-Operation-ID" not in r.headers for r in requests)
    assert all(all(0 < value <= 2 for value in r.extensions["timeout"].values()) for r in requests)


@pytest.mark.parametrize(
    "ready_payload,want_code", [(HEALTH, "not_ready"), ({}, "upstream_protocol_error")]
)
def test_ready_503_retains_valid_health_but_does_not_call_invalid_payload_a_health_snapshot(
    ready_payload,
    want_code,
):
    def handler(request):
        if request.url.path == "/p3/ready":
            return httpx.Response(503, json=ready_payload)
        return httpx.Response(
            200,
            json={"/p3/live": LIVE, "/p3/health": HEALTH, "/p3/capabilities": CAPABILITIES}[
                request.url.path
            ],
        )

    with client_for(handler) as client:
        status = client.status()
    assert not status.ready.ok and status.ready.status_code == 503
    assert status.ready.error.code == want_code
    if want_code == "not_ready":
        assert status.ready.data.readiness == "not_ready"
    else:
        assert status.ready.data is None


@pytest.mark.parametrize("probe", ["live", "health", "ready", "capabilities"])
def test_bad_success_probe_is_distinct_from_network_failure(probe):
    payloads = {"live": LIVE, "health": HEALTH, "ready": HEALTH, "capabilities": CAPABILITIES}
    with client_for(
        lambda request: httpx.Response(
            200,
            json=(
                {}
                if request.url.path == f"/p3/{probe}"
                else payloads[request.url.path.rsplit("/", 1)[1]]
            ),
        )
    ) as client:
        result = getattr(client.status(), probe)
    assert not result.ok and result.status_code == 200
    assert result.error.code == "upstream_protocol_error"


@pytest.mark.parametrize("outcome", ["empty", "degraded"])
def test_empty_and_degraded_recall_are_not_fabricated_or_upgraded(outcome):
    payload = dict(PACK)
    if outcome == "degraded":
        payload.update(
            outcome="degraded",
            coverage={"working": "partial", "long_term": "not_requested"},
            degradation_reasons=["部分记忆不可用"],
            rendered_context="我喜欢无糖咖啡",
            tokens_used=8,
            groups=[
                {
                    "group_id": "g-1",
                    "items": [
                        {
                            "memory": REF,
                            "content": "我喜欢无糖咖啡",
                            "sources": [SOURCE],
                            "representation": "original",
                        }
                    ],
                }
            ],
        )
    with client_for(lambda request: httpx.Response(200, json=payload)) as client:
        result = client.recall_result("r-1")
    assert result.outcome == outcome
    assert result.rendered_context == payload["rendered_context"]
    assert list(result.degradation_reasons) == payload["degradation_reasons"]
    assert not hasattr(result, "trace_id")


def test_unknown_processing_state_null_summaries_and_extra_payload_are_preserved_safely():
    with client_for(
        lambda request: httpx.Response(
            200,
            json={
                **PROCESSING,
                "state": "future_unknown_state",
                "artifact": {"secret": "must-not-appear"},
            },
        )
    ) as client:
        result = client.processing("m-1")
    assert result.state == "future_unknown_state"
    assert result.tasks[0].state is None and result.tasks[0].result_ref is None
    assert "must-not-appear" not in result.model_dump_json()
    with pytest.raises(ModelError):
        models.ProcessingData.model_validate({**PROCESSING, "historical_failed_tasks": -1})
