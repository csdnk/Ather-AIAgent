"""Public smoke must observe the original durable job after admission times out."""

import importlib.util
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize("script", ["smoke_service.py", "monitor_demo.py"])
@pytest.mark.parametrize("transient", [400, 503])
def test_public_command_polls_original_job_without_resubmission(script, transient, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/p3" / script
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("public_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                400, json={"code": "REQUEST_IN_PROGRESS"},
                headers={"Location": "/p3/operations/original", "X-P3-Job-ID": "original"},
            )
        assert request.url.path == "/p3/operations/original/result"
        if len(calls) == 2:
            code = "REQUEST_IN_PROGRESS" if transient == 400 else "DEPENDENCY_UNAVAILABLE"
            return httpx.Response(transient, json={"code": code})
        return httpx.Response(200, json={"saved": True, "operation_id": "original"})

    with httpx.Client(
        base_url="http://127.0.0.1:8080", transport=httpx.MockTransport(respond)
    ) as client:
        result = module.confirmed_request(
            client, "POST", "/p3/remember", timeout=2,
            headers={"X-Operation-ID": "original"}, json={"content": "original"},
        )
    assert result == {"saved": True, "operation_id": "original"}
    assert calls == [
        ("POST", "/p3/remember"),
        ("GET", "/p3/operations/original/result"),
        ("GET", "/p3/operations/original/result"),
    ]
