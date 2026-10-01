import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest

from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.server import build_demo_service

from .support import Upstream

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.2"])
def test_enabled_demo_refuses_public_bind(monkeypatch, bind):
    monkeypatch.setenv("AETHER_P4_DEMO_ENABLED", "1")
    with pytest.raises(ValueError, match="loopback"):
        build_demo_service(bind)


def test_disabled_is_default_and_missing_configuration_is_unavailable(monkeypatch):
    monkeypatch.delenv("AETHER_P4_DEMO_ENABLED", raising=False)
    monkeypatch.delenv("AETHER_P4_DEMO_CREDENTIAL_FILE", raising=False)
    service = build_demo_service("127.0.0.1")
    try:
        assert service.list_scenarios()["enabled"] is False
    finally:
        service.close()
    monkeypatch.setenv("AETHER_P4_DEMO_ENABLED", "1")
    service = build_demo_service("127.0.0.1")
    try:
        assert service.list_scenarios()["enabled"] is False
    finally:
        service.close()


def test_credential_file_stays_server_only(monkeypatch, tmp_path):
    credential = tmp_path / "credential"
    credential.write_text("local-test-secret", encoding="utf-8")
    monkeypatch.setenv("AETHER_P4_DEMO_ENABLED", "1")
    monkeypatch.setenv("AETHER_P4_DEMO_P3_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("AETHER_P4_DEMO_CREDENTIAL_FILE", str(credential))
    service = build_demo_service("127.0.0.1")
    try:
        assert service.list_scenarios()["enabled"] is True
        assert "local-test-secret" not in repr(service.list_scenarios())
    finally:
        service.close()


def test_configured_client_waits_for_slow_admission_over_real_socket(monkeypatch, tmp_path):
    """A healthy reply after P3's 30-second wait must not strand the demo."""
    upstream = Upstream()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_call(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = httpx.Request(
                self.command,
                "http://p3.test" + self.path,
                headers=list(self.headers.items()),
                content=body,
            )
            # Simulate only the upstream socket boundary, not the demo/client.
            if self.path == "/p3/remember" and not upstream.memories:
                time.sleep(31)
            response = upstream(request)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.wfile.write(response.content)

        do_GET = handle_call  # noqa: N815
        do_POST = handle_call  # noqa: N815

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    credential = tmp_path / "credential"
    credential.write_text("socket-test-secret", encoding="utf-8")
    monkeypatch.setenv("AETHER_P4_DEMO_ENABLED", "1")
    monkeypatch.setenv("AETHER_P4_DEMO_P3_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("AETHER_P4_DEMO_CREDENTIAL_FILE", str(credential))
    service = build_demo_service("127.0.0.1")
    try:
        run = service.start(StartRequest(scenario_id="library-basic", request_id=uuid4()))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            final = service.get(run.run_id)
            if final.state not in {"queued", "running"}:
                break
            time.sleep(0.05)
        assert final.state == "passed", final.error
        assert [step.state for step in final.steps] == ["passed"] * 6
        assert sum(r.url.path == "/p3/remember" for r in upstream.requests) == 3
    finally:
        service.close()
        server.shutdown()
        thread.join(2)
        server.server_close()
