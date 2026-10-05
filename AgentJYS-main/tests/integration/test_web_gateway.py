"""Exercise the shipped gateway against real HTTP, Temporal and current P2.

Set P3_TEST_NGINX_BINARY to a local nginx executable. Metadata/text processing
remain reference components; this does not certify the final Docker image.
"""

import asyncio
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from tests.integration.test_current_p2_http import (
    configuration as configuration,
)
from tests.integration.test_current_p2_http import eventually, headers

from aether_p4_simulator.server import P4Handler, P4HTTPServer, build_demo_service
from azure_component_service import Service

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def nginx_binary():
    value = os.environ.get("P3_TEST_NGINX_BINARY")
    if not value:
        if os.environ.get("P3_REQUIRE_NGINX"):
            pytest.fail("nginx executable is required for the gateway gate")
        pytest.skip("explicit P3_TEST_NGINX_BINARY required for real gateway validation")
    binary = Path(value).resolve()
    assert binary.is_file(), "configured nginx executable does not exist"
    return binary


def wait_http(client, path):
    until = time.monotonic() + 30
    while time.monotonic() < until:
        try:
            if client.get(path).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.03)
    raise AssertionError("HTTP endpoint did not become ready")


@pytest.fixture
def gateway(configuration, tmp_path, monkeypatch, nginx_binary, request):
    policy = configuration.remember
    p4_enabled = getattr(request, "param", None) == "p4_local"
    if hasattr(request, "param") and not p4_enabled:
        policy = policy.model_copy(update={"max_input_bytes": request.param})
    config = configuration.model_copy(update={"http_wait_seconds": 21, "remember": policy})
    service = Service(config)
    release = threading.Event()
    persist = service.runtime.remember.bodies.persist

    async def blocked(*args, **kwargs):
        while not release.is_set():
            await asyncio.sleep(0.02)
        return await persist(*args, **kwargs)

    monkeypatch.setattr(service.runtime.remember.bodies, "persist", blocked)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    backend_port = listener.getsockname()[1]
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        proxy_port = reservation.getsockname()[1]
    p4_server = None
    p4_thread = None
    demo = None
    if p4_enabled:
        release.set()
        credential = tmp_path / "demo-credential"
        credential.write_text("alice", encoding="utf-8")
        monkeypatch.setenv("AETHER_P4_DEMO_ENABLED", "1")
        monkeypatch.setenv("AETHER_P4_DEMO_P3_URL", f"http://127.0.0.1:{backend_port}")
        monkeypatch.setenv("AETHER_P4_DEMO_CREDENTIAL_FILE", str(credential))
        monkeypatch.setenv("AETHER_P4_DEMO_ORIGINS", json.dumps([f"http://127.0.0.1:{proxy_port}"]))
        demo = build_demo_service("127.0.0.1")
        p4_server = P4HTTPServer(("127.0.0.1", 0), P4Handler)
        p4_server.demo_service = demo
        p4_thread = threading.Thread(target=p4_server.serve_forever, daemon=True)
    server = uvicorn.Server(uvicorn.Config(service.app(), log_level="error", ws="none"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    prefix = tmp_path / "nginx"
    (prefix / "logs").mkdir(parents=True)
    (prefix / "temp").mkdir()
    shipped = (ROOT / "web/nginx.conf").read_text(encoding="utf-8")
    # Only bind addresses are adapted; request/response policies stay unmodified.
    configured = shipped.replace("listen 80;", f"listen 127.0.0.1:{proxy_port};")
    configured = configured.replace("http://p3:8080", f"http://127.0.0.1:{backend_port}")
    snippet = (ROOT / "web" / (
        "nginx.p4-local.conf" if p4_enabled else "nginx.p4-disabled.conf"
    )).read_text(encoding="utf-8")
    if p4_server is not None:
        snippet = snippet.replace("127.0.0.1:8090", f"127.0.0.1:{p4_server.server_port}")
    configured = configured.replace("include /etc/nginx/p4-location.conf;", snippet)
    (prefix / "nginx.conf").write_text(
        "worker_processes 1;\npid logs/nginx.pid;\nerror_log logs/error.log;\n"
        "events { worker_connections 64; }\nhttp {\naccess_log logs/access.log;\n"
        "client_body_temp_path temp/client_body;\nproxy_temp_path temp/proxy;\n"
        "fastcgi_temp_path temp/fastcgi;\nuwsgi_temp_path temp/uwsgi;\n"
        "scgi_temp_path temp/scgi;\n" + configured + "\n}\n",
        encoding="utf-8",
    )
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    args = [
        str(nginx_binary),
        "-p",
        prefix.as_posix() + "/",
        "-c",
        "nginx.conf",
        "-e",
        (prefix / "logs/error.log").as_posix(),
    ]
    process = None
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{backend_port}", timeout=3, trust_env=False
        ) as direct:
            wait_http(direct, "/p3/readyz")
        if p4_thread is not None:
            p4_thread.start()
        with (prefix / "logs/process.log").open("wb") as output:
            process = subprocess.Popen(
                args + ["-g", "daemon off;"], stdout=output, stderr=output, **flags
            )
            with httpx.Client(
                base_url=f"http://127.0.0.1:{proxy_port}", timeout=35, trust_env=False
            ) as client:
                wait_http(client, "/p3/readyz")
                yield client, release
    finally:
        release.set()
        if process is not None and process.poll() is None:
            subprocess.run(args + ["-s", "quit"], capture_output=True, timeout=10, **flags)
            process.wait(timeout=15)
        if p4_server is not None and p4_thread is not None:
            if p4_thread.is_alive():
                p4_server.shutdown()
                p4_thread.join(timeout=10)
            p4_server.server_close()
        if demo is not None:
            demo.close()
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive(), "owned HTTP server failed to stop"


def test_gateway_preserves_pending_job_and_original_result(gateway):
    client, release = gateway
    body = {
        "source": {
            "kind": "conversation",
            "external_id": "gateway-source",
            "external_version": "1",
            "occurred_at": "2026-10-03T00:00:00.000Z",
        },
        "selection": {"session_id": "gateway-session"},
        "content": {"kind": "text", "text": "Gateway pending result survives."},
    }
    accepted = client.post("/p3/remember", json=body, headers=headers("gateway-save"))
    assert accepted.status_code == 400, accepted.text
    assert accepted.json()["code"] == "REQUEST_IN_PROGRESS"
    assert accepted.headers["cache-control"] == "no-store"
    assert accepted.headers["x-content-type-options"] == "nosniff"
    location = accepted.headers["location"]
    job_id = accepted.headers["x-p3-job-id"]
    assert location == "/p3/operations/" + job_id
    assert client.get(location, headers=headers(user="eve")).status_code == 403
    release.set()

    def completed():
        response = client.get(location + "/result", headers=headers())
        if response.status_code == 400:
            assert response.json()["code"] == "REQUEST_IN_PROGRESS", response.text
            return None
        assert response.status_code == 200, response.text
        return response

    result = eventually(completed)
    repeated = client.post("/p3/remember", json=body, headers=headers("gateway-save"))
    assert repeated.status_code == 200, repeated.text
    assert repeated.headers["location"] == location
    assert repeated.headers["x-p3-job-id"] == job_id
    assert repeated.json() == result.json()


@pytest.mark.parametrize("path,status", [("/p3/live", 200), ("/p3/health", 401)])
def test_gateway_keeps_security_headers_on_api_success_and_error(gateway, path, status):
    client, _ = gateway
    response = client.get(path)
    assert response.status_code == status, response.text
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("cache-control") == "no-store"


def test_gateway_accepts_document_above_one_megabyte_with_exact_hash(gateway):
    from hashlib import sha256

    client, release = gateway
    release.set()
    data = b"x" * (2 * 1024 * 1024)
    path = "/p3/documents/gateway-document?version=1"
    request_headers = {**headers("gateway-document"), "Content-Type": "text/plain"}
    response = client.put(path, content=data, headers=request_headers)
    assert response.status_code == 200, response.text
    assert response.json()["expected_hash"] == sha256(data).hexdigest()
    repeated = client.put(path, content=data, headers=request_headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == response.json()
    assert repeated.headers["x-p3-job-id"] == response.headers["x-p3-job-id"]


@pytest.mark.parametrize("gateway", [1024], indirect=True)
def test_gateway_keeps_the_stricter_p3_document_limit(gateway):
    client, release = gateway
    release.set()
    response = client.put(
        "/p3/documents/too-large?version=1",
        content=b"x" * 2048,
        headers={**headers("gateway-too-large"), "Content-Type": "text/plain"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "INVALID_ARGUMENT"
    assert "x-p3-job-id" not in response.headers
    assert response.headers["cache-control"] == "no-store"


def test_default_gateway_reports_demo_disabled_without_exposing_a_runner(gateway):
    client, _ = gateway
    catalog = client.get("/p4-api/api/v1/demo/scenarios")
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["enabled"] is False
    assert catalog.json()["items"] == []
    assert catalog.headers["cache-control"] == "no-store"
    start = client.post("/p4-api/api/v1/demo/runs", json={})
    assert start.status_code == 503, start.text
    assert start.json()["error"]["code"] == "demo_unavailable"


@pytest.mark.parametrize("gateway", ["p4_local"], indirect=True)
def test_local_gateway_runs_p4_through_actual_p3_temporal_and_p2(gateway):
    client, _ = gateway
    catalog = client.get("/p4-api/api/v1/demo/scenarios")
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["enabled"] is True
    assert len(catalog.json()["items"]) == 5
    request = {"scenario_id": "library-basic", "request_id": str(uuid4())}
    denied = client.post(
        "/p4-api/api/v1/demo/runs", json=request, headers={"Origin": "https://outside.invalid"}
    )
    assert denied.status_code == 403, denied.text
    assert client.get("/p4-api/api/v1/agents").status_code == 404
    origin = str(client.base_url).rstrip("/")
    started = client.post(
        "/p4-api/api/v1/demo/runs", json=request, headers={"Origin": origin}
    )
    assert started.status_code == 202, started.text
    assert started.json()["run_id"] == request["request_id"]

    def finished():
        response = client.get("/p4-api/api/v1/demo/runs/" + request["request_id"])
        assert response.status_code == 200, response.text
        snapshot = response.json()
        if snapshot["state"] in {"queued", "running"}:
            return None
        assert snapshot["state"] == "passed", snapshot
        return snapshot

    final = eventually(finished)
    assert final["mode"]["object_storage"] == "p2_grpc"
    assert final["mode"]["scheduling"] == "temporal_v1"
    assert len(final["steps"]) == 6
    assert all(step["state"] == "passed" for step in final["steps"])
    repeated = client.post(
        "/p4-api/api/v1/demo/runs", json=request, headers={"Origin": origin}
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["run_id"] == request["request_id"]
    assert repeated.json()["steps"] == final["steps"]
