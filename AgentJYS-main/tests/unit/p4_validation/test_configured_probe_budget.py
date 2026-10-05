"""Deployment probe budgets cover a real socket without changing command timeouts."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from aether_p4_simulator.validation.client import P3ValidationClient


def test_configured_probe_budget_waits_for_slow_live_reply():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            time.sleep(0.15)
            body = b'{"liveness":"alive","checked_at":"2026-10-04T00:00:00.000Z"}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = P3ValidationClient(
        f'http://127.0.0.1:{server.server_port}', 'test',
        timeout_seconds=2, probe_timeout_seconds=0.5,
    )
    try:
        assert client.status().live.ok
    finally:
        client.close()
        server.shutdown()
        thread.join(2)
        server.server_close()


@pytest.mark.parametrize('seconds', [0, -1, 61, float('nan'), float('inf')])
def test_invalid_probe_budget_never_sends_requests(seconds):
    calls = []
    with pytest.raises(ValueError):
        P3ValidationClient('http://p3.test', 'test', probe_timeout_seconds=seconds,
                           transport=httpx.MockTransport(lambda request: calls.append(request)))
    assert not calls
