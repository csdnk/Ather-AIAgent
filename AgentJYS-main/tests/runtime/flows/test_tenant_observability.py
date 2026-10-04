"""Tenant, JWT and observability boundary tests; no live P2 or Temporal required."""

import asyncio
import json
import os
import time
from hashlib import sha256
from types import SimpleNamespace

import httpx
import jwt
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, Principal, Scope
from aether_agent_memory.runtime.flows.config import (
    BrowserIdentityConfiguration,
    IdentityConfiguration,
    JWTIssuerConfiguration,
)
from aether_agent_memory.runtime.flows.jwt_auth import JWTAuthenticator
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.identity import jwt_issuer_policy_hash
from azure_component_service import Service
from azure_test_runtime import Foundation
from component_configuration import ComponentConfiguration

ISSUER = "https://identity.example"
SECRET = "private-body-and-token-marker"


@pytest.fixture(autouse=True)
def isolated_export_environment(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("OTEL_"):
            monkeypatch.delenv(key)


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def person(name="alice", tenant="t1", epoch=1):
    return Principal(
        principal_id=name,
        home_scope=Scope(tenant_id=tenant, application_id="app", user_id=name, agent_id="agent"),
        permissions=tuple(Permission),
        auth_epoch=epoch,
    )


def issuer(**updates):
    return JWTIssuerConfiguration.model_validate(
        {
            "issuer": ISSUER,
            "jwks_url": ISSUER + "/keys",
            "audience": "p3",
            "subject_mappings": [{"subject": "external-alice", "principal_id": "alice"}],
            "leeway_seconds": 0,
            **updates,
        }
    )


def token(key, *, kid="key-1", **updates):
    return jwt.encode(
        {
            "iss": ISSUER,
            "sub": "external-alice",
            "aud": "p3",
            "exp": int(time.time()) + 300,
            **updates,
        },
        key,
        algorithm="RS256",
        headers={"kid": kid},
    )


def identity_document(*, revision=1, enabled=True, epoch=1, static=True, **issuer_updates):
    entries = []
    for name, tenant in [("alice", "t1"), ("bob", "t2")]:
        principal = person(name, tenant, epoch)
        item = {"principal": principal.model_dump(mode="json")}
        if static or name == "bob":
            item["credential_sha256"] = sha256(name.encode()).hexdigest()
        entries.append(item)
    return {
        "revision": revision,
        "tenants": [{"tenant_id": "t1", "enabled": enabled}, {"tenant_id": "t2"}],
        "identities": entries,
        "jwt_issuers": [issuer(**issuer_updates).model_dump(mode="json")],
    }


@pytest.fixture
def foundation(tmp_path):
    host = Foundation(tmp_path / "p3.db")
    yield host
    host.close()


def provision(host, cfg=None, *, epoch=1, enabled=True, static=True):
    cfg = cfg or issuer()
    host.identity.provision(
        [
            (sha256(b"alice").hexdigest() if static else None, person(epoch=epoch)),
            (sha256(b"bob").hexdigest(), person("bob", "t2")),
        ],
        tenants={"t1": enabled, "t2": True},
        jwt_subjects=[(cfg.issuer, "external-alice", "alice")],
        jwt_issuers=[cfg.model_dump(mode="json")],
    )


@pytest.fixture
def authority(signing_key):
    state = {"status": 200, "keys": [], "calls": 0, "now": 10.0}
    state["keys"] = [
        {
            **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())),
            "kid": "key-1",
            "alg": "RS256",
            "use": "sig",
        }
    ]

    def respond(request):
        assert str(request.url) == ISSUER + "/keys"
        state["calls"] += 1
        return httpx.Response(state["status"], json={"keys": state["keys"]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        auth = JWTAuthenticator(client=client, clock=lambda: state["now"])
        auth.configure([issuer()])
        yield auth, state
        auth.close()


def test_jwt_uses_server_scope_and_supports_jwt_only_principal(foundation, authority, signing_key):
    auth, state = authority
    provision(foundation, static=False)
    signed = token(signing_key, tenant_id="t2", permissions=["admin"], role="root")
    p = auth.authenticate(foundation.identity, signed)
    assert p.home_scope.tenant_id == "t1"
    assert p == person()
    assert auth.authenticate(foundation.identity, signed) == p
    assert state["calls"] == 1
    with pytest.raises(FoundationError) as error:
        foundation.identity.authenticate("alice")
    assert error.value.code == ErrorCode.UNAUTHENTICATED


@pytest.mark.parametrize(
    "updates",
    [
        {"exp": 1},
        {"aud": "another-service"},
        {"iss": "https://evil.example"},
        {"sub": "unmapped-user"},
        {"nbf": int(time.time()) + 3600},
        {"sub": 42},
    ],
)
def test_invalid_jwt_is_rejected(foundation, authority, signing_key, updates):
    auth, _ = authority
    provision(foundation)
    with pytest.raises(FoundationError) as error:
        auth.authenticate(foundation.identity, token(signing_key, **updates))
    assert error.value.code == ErrorCode.UNAUTHENTICATED


@pytest.mark.parametrize(
    "credential",
    ["garbage", "a.b.c", "", "a" * 65537],
    ids=["garbage", "malformed", "empty", "oversize"],
)
def test_malformed_tokens_never_fetch_keys(foundation, authority, credential):
    auth, state = authority
    provision(foundation)
    with pytest.raises(FoundationError) as error:
        auth.authenticate(foundation.identity, credential)
    assert error.value.code == ErrorCode.UNAUTHENTICATED
    assert state["calls"] == 0


def test_disallowed_algorithm_and_missing_required_claim(foundation, authority, signing_key):
    auth, state = authority
    provision(foundation)
    hs = jwt.encode(
        {"iss": ISSUER, "sub": "external-alice", "aud": "p3", "exp": int(time.time()) + 300},
        "x" * 64,
        algorithm="HS256",
        headers={"kid": "key-1"},
    )
    with pytest.raises(FoundationError):
        auth.authenticate(foundation.identity, hs)
    assert state["calls"] == 0
    no_exp = jwt.encode(
        {"iss": ISSUER, "sub": "external-alice", "aud": "p3"},
        signing_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    with pytest.raises(FoundationError):
        auth.authenticate(foundation.identity, no_exp)


def test_unknown_kid_is_401_and_refresh_is_bounded(foundation, authority, signing_key):
    auth, state = authority
    provision(foundation)
    auth.authenticate(foundation.identity, token(signing_key))
    for number in range(15):
        with pytest.raises(FoundationError) as error:
            auth.authenticate(foundation.identity, token(signing_key, kid=f"unknown-{number}"))
        assert error.value.code == ErrorCode.UNAUTHENTICATED
    assert state["calls"] == 1
    state["now"] += 6
    state["keys"][0]["kid"] = "key-2"
    assert auth.authenticate(foundation.identity, token(signing_key, kid="key-2")) == person()
    assert state["calls"] == 2


def test_jwks_outage_is_503_and_expired_cache_is_not_trusted(foundation, authority, signing_key):
    auth, state = authority
    provision(foundation)
    signed = token(signing_key)
    auth.authenticate(foundation.identity, signed)
    state["status"] = 503
    assert auth.authenticate(foundation.identity, signed) == person()
    state["now"] += 301
    with pytest.raises(FoundationError) as error:
        auth.authenticate(foundation.identity, signed)
    assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE


def test_disabled_tenant_is_forbidden_and_old_context_stays_revoked(
    foundation, authority, signing_key
):
    auth, _ = authority
    provision(foundation)
    ctx = foundation.identity.context("alice")
    provision(foundation, enabled=False)
    for authenticate in (
        lambda: auth.authenticate(foundation.identity, token(signing_key)),
        lambda: foundation.identity.authenticate("alice"),
    ):
        with pytest.raises(FoundationError) as error:
            authenticate()
        assert error.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(ValueError, match="fresh principal auth_epoch"):
        provision(foundation)
    provision(foundation, epoch=2)
    assert auth.authenticate(foundation.identity, token(signing_key)).auth_epoch == 2
    with pytest.raises(FoundationError), foundation.uow.transaction() as tx:
        foundation.identity.revalidate(tx, ctx)


def test_policy_change_rejects_stale_inflight_verifier(foundation, authority, signing_key):
    auth, _ = authority
    provision(foundation)
    auth.authenticate(foundation.identity, token(signing_key))
    changed = issuer(audience="p3-next")
    provision(foundation, changed, epoch=2)
    with pytest.raises(FoundationError):
        auth.authenticate(foundation.identity, token(signing_key))
    auth.configure([changed])
    assert auth.authenticate(foundation.identity, token(signing_key, aud="p3-next")).auth_epoch == 2
    assert jwt_issuer_policy_hash(issuer().model_dump(mode="json")) != jwt_issuer_policy_hash(
        issuer(algorithms=["ES256"]).model_dump(mode="json")
    )


def test_legacy_identity_revision_is_compatible(foundation):
    principals = [(sha256(b"alice").hexdigest(), person())]
    legacy = {
        "principals": [(d, p.model_dump(mode="json")) for d, p in principals],
        "grants": [],
        "tenants": {"t1": True},
    }
    with foundation.uow.transaction() as tx:
        tx.write("settings", "identity_revision", {"revision": 1, "signature": fingerprint(legacy)})
    foundation.identity.provision(principals, tenants={"t1": True}, configuration_revision=1)
    assert foundation.identity.authenticate("alice") == person()


@pytest.mark.parametrize(
    "updates",
    [
        {"algorithms": []},
        {"algorithms": ["HS256"]},
        {"algorithms": ["RS256", "RS256"]},
        {"jwks_url": "http://identity.example/keys"},
        {"subject_mappings": []},
    ],
)
def test_unsafe_issuer_config_is_rejected(updates):
    with pytest.raises(ValidationError):
        issuer(**updates)


def test_orphan_jwt_principal_config_is_rejected():
    document = identity_document(static=False)
    document["jwt_issuers"] = []
    with pytest.raises(ValidationError):
        IdentityConfiguration.model_validate(document)


@pytest.fixture
def web_service(tmp_path, authority):
    auth, state = authority
    path = tmp_path / "identity.yaml"
    path.write_text(yaml.safe_dump(identity_document()), encoding="utf-8")
    service = Service(
        ComponentConfiguration(
            temporal={"deployment_id": "tenant-tests", "endpoint": "127.0.0.1:7233"},
            identity_file=path,
            data_dir=tmp_path / "state",
            embedding_profile="injected",
        )
    )
    service.jwt_auth.close()
    service.jwt_auth = auth
    exporter = InMemorySpanExporter()
    service.tracing_provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = service.app()

    @app.get("/_test/context/{item}")
    def show_context(item: str, ctx=app.state.trusted_dependency):
        with service.runtime.foundation.telemetry.span(
            ctx, "operate.evaluate", {"text": SECRET}
        ) as node:
            node.output = {"content": SECRET}
            return {
                "tenant": ctx.principal.home_scope.tenant_id,
                "trace_id": ctx.trace_id,
                "span_id": node.span_id,
                "parent_id": ctx.span_id,
                "request_id": ctx.request_id,
            }

    @app.get("/_test/fail")
    def fail(ctx=app.state.trusted_dependency):
        with service.runtime.foundation.telemetry.span(ctx, "test.failure"):
            raise RuntimeError(SECRET)

    client = TestClient(app, raise_server_exceptions=False)
    yield SimpleNamespace(service=service, app=app, client=client, exporter=exporter, state=state)
    client.close()
    asyncio.run(service.close())


def test_http_jwt_trace_and_json_logs_are_linked_and_redacted(web_service, signing_key, capsys):
    s = web_service
    trace_id = "1234567890abcdef1234567890abcdef"
    parent = "1234567890abcdef"
    signed = token(signing_key, tenant_id="t2")
    result = s.client.get(
        "/_test/context/" + SECRET + "?password=" + SECRET,
        headers={
            "Authorization": "Bearer " + signed,
            "x-request-id": "request-123",
            "traceparent": f"00-{trace_id}-{parent}-01",
        },
    )
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["tenant"] == "t1"
    assert result.headers["x-trace-id"] == data["trace_id"] == trace_id
    assert result.headers["x-request-id"] == data["request_id"] == "request-123"
    spans = s.exporter.get_finished_spans()
    node = next(span for span in spans if span.name == "operate.evaluate")
    server = next(span for span in spans if span.name.startswith("GET "))
    assert server.parent.span_id == int(parent, 16)
    assert node.parent.span_id == server.context.span_id
    assert node.context.span_id == int(data["span_id"], 16)
    ctx = s.service.runtime.foundation.identity.context("alice")
    records = s.service.runtime.foundation.diagnostics.trace(ctx, trace_id)["records"]
    assert any(r["span_id"] == data["span_id"] for r in records)
    assert not s.service.runtime.foundation.diagnostics.trace(
        s.service.runtime.foundation.identity.context("bob"), trace_id
    )["records"]
    captured = capsys.readouterr().out
    assert SECRET not in captured and signed not in captured
    assert SECRET not in json.dumps(records)
    assert SECRET not in str([(dict(span.attributes), list(span.events)) for span in spans])
    access = [json.loads(line) for line in captured.splitlines() if '"http_request"' in line][-1]
    assert access["route"] == "/_test/context/{item}"
    assert access["trace_id"] == trace_id and access["tenant_id"] == "t1"


def test_http_failure_does_not_export_exception_message(web_service, capsys):
    s = web_service
    response = s.client.get("/_test/fail", headers={"Authorization": "Bearer alice"})
    assert response.status_code == 500
    assert len(response.headers["x-trace-id"]) == 32
    assert response.headers["x-request-id"]
    assert SECRET not in response.text
    spans = s.exporter.get_finished_spans()
    assert any(span.status.status_code.name == "ERROR" for span in spans)
    assert SECRET not in capsys.readouterr().out
    assert SECRET not in str([(dict(span.attributes), list(span.events)) for span in spans])


def test_http_static_fallback_preserves_forbidden_and_dependency_errors(
    web_service, signing_key, monkeypatch
):
    s = web_service
    assert (
        s.client.get("/_test/context/static", headers={"Authorization": "Bearer alice"}).status_code
        == 200
    )
    s.state["status"] = 503
    assert (
        s.client.get(
            "/_test/context/jwt", headers={"Authorization": "Bearer " + token(signing_key)}
        ).status_code
        == 503
    )
    for code, status in [(ErrorCode.FORBIDDEN, 403), (ErrorCode.DEPENDENCY_UNAVAILABLE, 503)]:

        def deny(_credential, error_code=code):
            raise FoundationError(error_code, "static authentication failed")

        monkeypatch.setattr(s.service.runtime.foundation.identity, "authenticate", deny)
        before = s.state["calls"]
        result = s.client.get("/_test/context/static", headers={"Authorization": "Bearer alice"})
        assert result.status_code == status
        assert s.state["calls"] == before


def test_http_bad_context_and_admission_failure_are_logged(web_service, capsys):
    s = web_service
    result = s.client.get(
        "/_test/context/item", headers={"traceparent": "bad", "x-request-id": "bad id"}
    )
    assert result.status_code == 401
    assert result.headers["x-request-id"] == result.json()["request_id"]
    assert len(result.headers["x-trace-id"]) == 32
    assert result.headers["x-request-id"] != "bad id"
    post = s.client.post("/p3/remember", json={"text": SECRET})
    assert post.status_code == 503
    assert post.headers["x-request-id"] == post.json()["request_id"]
    assert SECRET not in capsys.readouterr().out


def test_service_reload_and_mixed_closers(web_service, signing_key):
    s = web_service.service
    path = s.config.identity_file
    path.write_text(yaml.safe_dump(identity_document(revision=2, enabled=False)), encoding="utf-8")
    s.reload_identity()
    result = web_service.client.get(
        "/_test/context/item", headers={"Authorization": "Bearer " + token(signing_key)}
    )
    assert result.status_code == 403
    path.write_text(yaml.safe_dump(identity_document(revision=1)), encoding="utf-8")
    with pytest.raises(ValueError, match="configuration must advance"):
        s.reload_identity()
    order = []

    async def async_close():
        order.append("async")

    s.closers.extend([lambda: order.append("sync"), async_close])
    asyncio.run(s.close())
    asyncio.run(s.close())
    assert order == ["async", "sync"]


def test_durable_context_continues_trace_after_restart(foundation, tmp_path):
    provision(foundation)
    ctx = foundation.identity.context("alice")
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    foundation.telemetry.set_tracer(provider.get_tracer("test"))
    with foundation.telemetry.span(ctx, "task.producer") as node:
        persisted = foundation.telemetry.producer_context(ctx).model_dump_json()
        producer_span = node.span_id
    from aether_agent_memory.runtime.contracts.models import TrustedContext
    from azure_test_runtime import Telemetry

    log = Telemetry(tmp_path / "restarted.logs.db")
    log.set_tracer(provider.get_tracer("restarted"))
    restored = TrustedContext.model_validate_json(persisted)
    try:
        with log.span(restored, "task.resumed"):
            pass
        resumed = exporter.get_finished_spans()[-1]
        assert resumed.context.trace_id == int(ctx.trace_id, 16)
        assert resumed.parent.span_id == int(producer_span, 16)
    finally:
        provider.shutdown()
        log.close()


def test_otlp_http_exports_real_protobuf_with_linked_redacted_spans(foundation):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from queue import Queue
    from threading import Thread

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    from aether_agent_memory.runtime.flows.observability import configure_tracing

    received = Queue()

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            received.put((self.path, self.headers["Content-Type"], payload))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = None
    try:
        provision(foundation)
        ctx = foundation.identity.context("alice")
        provider = configure_tracing(
            foundation.telemetry, f"http://127.0.0.1:{server.server_port}/v1/traces"
        )
        with foundation.telemetry.span(ctx, "otlp.test", {"content": SECRET}) as node:
            node.output = {"text": SECRET}
        assert provider.force_flush(timeout_millis=5000)
        path, content_type, payload = received.get(timeout=5)
        assert path == "/v1/traces"
        assert content_type == "application/x-protobuf"
        message = ExportTraceServiceRequest.FromString(payload)
        spans = [
            span
            for resource in message.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        ]
        exported = next(span for span in spans if span.name == "otlp.test")
        assert exported.trace_id.hex() == ctx.trace_id
        assert exported.span_id.hex() == node.span_id
        assert exported.parent_span_id.hex() == ctx.span_id
        assert SECRET.encode() not in payload
    finally:
        if provider is not None:
            provider.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_jwt_wrong_signature_is_rejected(foundation, authority):
    auth, _ = authority
    provision(foundation)
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(FoundationError) as error:
        auth.authenticate(foundation.identity, token(wrong_key))
    assert error.value.code == ErrorCode.UNAUTHENTICATED


@pytest.mark.parametrize("problem", ["private", "duplicate", "empty", "bad_key"])
def test_invalid_jwks_fails_closed(foundation, authority, signing_key, problem):
    auth, state = authority
    provision(foundation)
    if problem == "private":
        state["keys"][0]["d"] = "private-value"
    elif problem == "duplicate":
        state["keys"].append(dict(state["keys"][0]))
    elif problem == "empty":
        state["keys"] = []
    else:
        state["keys"][0]["kty"] = "unsupported"
    with pytest.raises(FoundationError) as error:
        auth.authenticate(foundation.identity, token(signing_key))
    assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE


def test_request_logger_failure_does_not_fail_response(foundation):
    from aether_agent_memory.runtime.flows.observability import (
        RequestObservability,
        configure_tracing,
    )

    class BrokenLogger:
        def info(self, *args, **kwargs):
            raise OSError(SECRET)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    provider = configure_tracing(foundation.telemetry)
    try:
        client = TestClient(
            RequestObservability(app, telemetry=foundation.telemetry, logger=BrokenLogger())
        )
        try:
            result = client.get("/")
            assert result.status_code == 204
            assert len(result.headers["x-trace-id"]) == 32
        finally:
            client.close()
    finally:
        provider.shutdown()


def test_shutdown_failure_still_closes_resources_and_releases_lock(web_service, monkeypatch):
    from aether_agent_memory.runtime.temporal.locking import DirectoryLock

    service = web_service.service
    closed = []
    service.closers.append(lambda: closed.append("closed"))

    async def failed_stop():
        raise RuntimeError("worker shutdown failed")

    monkeypatch.setattr(service.supervisor, "stop", failed_stop)
    with pytest.raises(RuntimeError, match="worker shutdown failed"):
        asyncio.run(service.close())
    assert closed == ["closed"]
    lock = DirectoryLock()
    try:
        lock.acquire(service.config.data_dir)
    finally:
        lock.release()


def test_startup_failure_closes_new_auth_trace_resources(tmp_path, monkeypatch):
    path = tmp_path / "identity.yaml"
    path.write_text(yaml.safe_dump(identity_document()), encoding="utf-8")
    config = ComponentConfiguration(
        temporal={"deployment_id": "startup-test", "endpoint": "127.0.0.1:7233"},
        identity_file=path,
        data_dir=tmp_path / "state",
        embedding_profile="injected",
    )
    original_reload = Service.reload_identity
    closed = []

    from opentelemetry.sdk.trace import TracerProvider

    from aether_agent_memory.runtime.flows.jwt_auth import JWTAuthenticator
    from azure_test_runtime import ThreeFlows

    # Instrument before construction: Service owns the bound closers it registers.
    for label, owner, method in (
        ("jwt", JWTAuthenticator, "close"),
        ("tracing", TracerProvider, "shutdown"),
        ("runtime", ThreeFlows, "close"),
    ):
        original = getattr(owner, method)

        def close(self, original=original, label=label):
            closed.append(label)
            original(self)

        monkeypatch.setattr(owner, method, close)

    def fail_reload(service):
        raise ValueError("identity reload failed")

    monkeypatch.setattr(Service, "reload_identity", fail_reload)
    with pytest.raises(ValueError, match="identity reload failed"):
        Service(config)
    assert closed == ["tracing", "jwt", "runtime"]
    monkeypatch.setattr(Service, "reload_identity", original_reload)
    retry = Service(config)
    asyncio.run(retry.close())


def test_http_lifespan_runs_cleanup_when_worker_stop_fails(web_service, monkeypatch):
    service = web_service.service
    closed = []
    service.closers.append(lambda: closed.append("closed"))

    async def start():
        pass

    async def stop():
        raise RuntimeError("worker stop failed")

    monkeypatch.setattr(service.execution, "start", start)
    monkeypatch.setattr(service.execution, "stop", stop)
    with pytest.raises(RuntimeError, match="worker stop failed"), TestClient(web_service.app):
        pass
    assert closed == ["closed"]


# Browser login is an optional identity entry; authorization stays in P3.
def test_browser_login_disabled_keeps_static_identity_available(web_service):
    public = web_service.client.get("/p3/auth/config")
    assert public.status_code == 200
    assert public.json() == {"enabled": False}
    assert public.headers["cache-control"] == "no-store"
    me = web_service.client.get("/p3/auth/me", headers={"Authorization": "Bearer alice"})
    assert me.status_code == 200
    assert me.json()["principal_id"] == "alice"
    assert me.headers["cache-control"] == "no-store"


def test_browser_configuration_exposes_only_public_client_fields(web_service):
    browser = BrowserIdentityConfiguration(
        url="http://127.0.0.1:18080", realm="p3-demo", client_id="p3-monitor"
    )
    web_service.service.config = web_service.service.config.model_copy(
        update={"browser_identity": browser}
    )
    client = TestClient(web_service.service.app())
    try:
        result = client.get("/p3/auth/config")
    finally:
        client.close()
    assert result.status_code == 200
    assert result.json() == {
        "enabled": True,
        "url": "http://127.0.0.1:18080/",
        "realm": "p3-demo",
        "client_id": "p3-monitor",
    }
    assert result.headers["cache-control"] == "no-store"


def test_current_identity_ignores_client_tenant_and_permission_claims(web_service, signing_key):
    signed = token(signing_key, tenant_id="t2", permissions=["admin"])
    result = web_service.client.get(
        "/p3/auth/me?tenant_id=t2", headers={"Authorization": "Bearer " + signed}
    )
    assert result.status_code == 200
    assert result.json() == {
        "principal_id": "alice",
        "scope": person().home_scope.model_dump(mode="json"),
        "permissions": sorted(permission.value for permission in person().permissions),
    }
    assert result.headers["cache-control"] == "no-store"
    assert web_service.client.get("/p3/auth/me").status_code == 401


@pytest.mark.parametrize(
    "url",
    [
        "http://identity.example",
        "https://user:secret@identity.example",
        "https://identity.example?token=private",
        "https://identity.example#private",
    ],
)
def test_browser_identity_rejects_unsafe_urls(url):
    with pytest.raises(ValidationError):
        BrowserIdentityConfiguration(url=url, realm="p3-demo", client_id="p3-monitor")


def test_browser_identity_rejects_secret_and_invalid_realm():
    for extra in [{"client_secret": "private"}, {"realm": "../another"}]:
        with pytest.raises(ValidationError):
            BrowserIdentityConfiguration.model_validate(
                {
                    "url": "https://identity.example",
                    "realm": "p3-demo",
                    "client_id": "p3-monitor",
                    **extra,
                }
            )
    browser = BrowserIdentityConfiguration(
        url="https://identity.example/auth/", realm="p3-demo", client_id="p3-monitor"
    )
    assert browser.issuer == "https://identity.example/auth/realms/p3-demo"


def test_browser_login_requires_matching_backend_jwt_issuer(tmp_path):
    identity_file = tmp_path / "identity.yaml"
    document = identity_document()
    identity_file.write_text(yaml.safe_dump(document), encoding="utf-8")
    config = ComponentConfiguration(
        temporal={"deployment_id": "browser-test", "endpoint": "127.0.0.1:7233"},
        identity_file=identity_file,
        data_dir=tmp_path / "state",
        embedding_profile="injected",
        browser_identity={
            "url": "https://identity.example",
            "realm": "p3-demo",
            "client_id": "p3-monitor",
        },
    )
    with pytest.raises(ValueError, match="configured JWT verifier"):
        Service(config)
    document["jwt_issuers"][0]["issuer"] = config.browser_identity.issuer
    document["jwt_issuers"][0]["jwks_url"] = config.browser_identity.issuer + "/keys"
    identity_file.write_text(yaml.safe_dump(document), encoding="utf-8")
    service = Service(config)
    asyncio.run(service.close())
