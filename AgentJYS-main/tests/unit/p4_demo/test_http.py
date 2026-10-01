import json
import socket
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest

from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.server import P4Handler

from .support import Upstream, finish
from .test_service import service_for

pytestmark = pytest.mark.unit
ORIGIN = "http://127.0.0.1:5173"


@contextmanager
def serve(service):
    server = ThreadingHTTPServer(("127.0.0.1", 0), P4Handler)
    server.demo_service = service
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(2)
        server.server_close()
        service.close()


def payload():
    return {"scenario_id": "library-basic", "request_id": str(uuid4())}


def test_three_demo_routes_preserve_legacy_and_no_secret():
    upstream = Upstream()
    service = service_for(upstream)
    with serve(service) as base, httpx.Client(base_url=base, trust_env=False) as http:
        assert http.get("/api/v1/agents").status_code == 200
        scenarios = http.get("/api/v1/demo/scenarios")
        assert scenarios.status_code == 200
        assert scenarios.json()["items"][0]["id"] == "library-full"
        assert len(scenarios.json()["items"]) == 5
        assert scenarios.headers["cache-control"] == "no-store"
        req = payload()
        started = http.post("/api/v1/demo/runs", json=req, headers={"Origin": ORIGIN})
        assert started.status_code == 202
        final = finish(service, started.json()["run_id"])
        assert final.state == "passed"
        repeated = http.post("/api/v1/demo/runs", json=req, headers={"Origin": ORIGIN})
        assert repeated.status_code == 200
        result = http.get("/api/v1/demo/runs/" + req["request_id"])
        assert result.status_code == 200
        assert result.json()["state"] == "passed"
        assert "secret" not in result.text
        assert sum(r.url.path == "/p3/remember" for r in upstream.requests) == 3
        assert http.get("/api/v1/demo/runs/" + str(uuid4())).status_code == 404


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Origin": "https://outside.invalid"}, 403),
        ({"Origin": "null"}, 403),
        ({}, 403),
        ({"Origin": ORIGIN, "Host": "outside.invalid"}, 403),
        ({"Origin": ORIGIN, "Content-Type": "text/plain"}, 415),
        ({"Origin": ORIGIN, "Content-Length": "5000"}, 413),
        ({"Origin": ORIGIN, "Content-Length": "nope"}, 400),
        ({"Origin": ORIGIN, "Transfer-Encoding": "chunked"}, 400),
    ],
)
def test_rejected_http_envelopes_never_contact_p3(headers, status):
    upstream = Upstream()
    with serve(service_for(upstream)) as base:
        # Raw socket exercises malformed framing that HTTPX correctly refuses to send.
        port = int(base.rsplit(":", 1)[1])
        body = json.dumps(payload()).encode()
        all_headers = {
            "Host": f"127.0.0.1:{port}",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            **headers,
        }
        wire = (
            b"POST /api/v1/demo/runs HTTP/1.1\r\n"
            + "".join(f"{k}: {v}\r\n" for k, v in all_headers.items()).encode()
            + b"\r\n"
            + body
        )
        with socket.create_connection(("127.0.0.1", port), timeout=7) as sock:
            sock.sendall(wire)
            response = sock.recv(4096)
        assert int(response.split(b" ")[1]) == status
        assert upstream.requests == []


@pytest.mark.parametrize(
    "body",
    [
        {"text": "hello"},
        {"base_url": "http://outside"},
        {"credential": "secret"},
        {"selection": {}},
        {"memory_id": "m1"},
    ],
)
def test_extra_inputs_cannot_turn_script_into_proxy(body):
    upstream = Upstream()
    with serve(service_for(upstream)) as base, httpx.Client(trust_env=False) as http:
        response = http.post(
            base + "/api/v1/demo/runs", json={**payload(), **body}, headers={"Origin": ORIGIN}
        )
        assert response.status_code == 422
        assert "secret" not in response.text
        assert upstream.requests == []


@pytest.mark.parametrize(
    "body,status",
    [
        ("{broken", 400),
        ("[]", 422),
        ('{"scenario_id":"library-basic","request_id":"bad"}', 422),
    ],
)
def test_invalid_body_is_safe(body, status):
    upstream = Upstream()
    with serve(service_for(upstream)) as base, httpx.Client(trust_env=False) as http:
        response = http.post(
            base + "/api/v1/demo/runs",
            content=body,
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        assert response.status_code == status
        assert upstream.requests == []


def test_disabled_service_and_cors_preflight():
    with serve(DemoService(None)) as base, httpx.Client(trust_env=False) as http:
        assert http.get(base + "/api/v1/demo/scenarios").json()["enabled"] is False
        response = http.post(base + "/api/v1/demo/runs", json=payload(), headers={"Origin": ORIGIN})
        assert response.status_code == 503
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["x-content-type-options"] == "nosniff"
        assert (
            http.options(base + "/api/v1/demo/runs", headers={"Origin": ORIGIN}).status_code == 204
        )
        denied = http.get(base + "/api/v1/demo/scenarios", headers={"Origin": "https://evil.test"})
        assert denied.status_code == 403
        assert "access-control-allow-origin" not in denied.headers


@pytest.mark.parametrize(
    "extra",
    [
        "Content-Length: 1\r\n",
        "Host: evil.test\r\n",
        "Origin: https://evil.test\r\n",
    ],
)
def test_duplicate_security_headers_rejected(extra):
    upstream = Upstream()
    with serve(service_for(upstream)) as base:
        port = int(base.rsplit(":", 1)[1])
        wire = (
            "POST /api/v1/demo/runs HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\nOrigin: {ORIGIN}\r\n"
            f"Content-Type: application/json\r\nContent-Length: 2\r\n{extra}\r\n{{}}"
        ).encode()
        with socket.create_connection(("127.0.0.1", port), timeout=7) as sock:
            sock.sendall(wire)
            response = sock.recv(4096)
        assert int(response.split(b" ")[1]) in {400, 403}
        assert upstream.requests == []


def test_incomplete_body_times_out_without_writing():
    upstream = Upstream()
    with serve(service_for(upstream)) as base:
        port = int(base.rsplit(":", 1)[1])
        wire = (
            "POST /api/v1/demo/runs HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\nOrigin: {ORIGIN}\r\n"
            "Content-Type: application/json\r\nContent-Length: 10\r\n\r\n{"
        ).encode()
        with socket.create_connection(("127.0.0.1", port), timeout=7) as sock:
            sock.sendall(wire)
            response = sock.recv(4096)
        assert int(response.split(b" ")[1]) == 408
        assert upstream.requests == []
