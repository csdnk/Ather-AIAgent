"""Real P4 HTTP -> P3 ASGI/lifespan -> persistent Temporal Server/Worker."""

import asyncio
import hashlib
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from aether_agent_memory.runtime.contracts.models import Permission
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.server import P4Handler
from aether_p4_simulator.validation.client import P3ValidationClient
from azure_component_service import Service
from component_configuration import ComponentConfiguration as ServiceConfiguration

pytestmark = pytest.mark.integration
PREFIX = "/api/v1/demo"


def configuration(tmp_path, endpoint):
    identity = tmp_path / "identities.yaml"
    identity.write_text(
        yaml.safe_dump(
            {
                "revision": 1,
                "tenants": [{"tenant_id": "t1"}, {"tenant_id": "t2"}],
                "identities": [
                    {
                        "credential_sha256": hashlib.sha256(user.encode()).hexdigest(),
                        "principal": {
                            "principal_id": user,
                            "auth_epoch": 1,
                            "permissions": [p.value for p in Permission],
                            "home_scope": {
                                "tenant_id": tenant,
                                "application_id": "app",
                                "user_id": user,
                                "agent_id": user,
                            },
                        },
                    }
                    for user, tenant in (("alice", "t1"), ("eve", "t2"))
                ],
            }
        ),
        encoding="utf-8",
    )
    return ServiceConfiguration(
        data_dir=tmp_path / "state",
        identity_file=identity,
        embedding_profile="injected",
        periodic_seconds=0.2,
        poll_seconds=0.02,
        identity_reload_seconds=0.1,
        shutdown_seconds=1,
        http_wait_seconds=0.05,
        temporal={
            "deployment_id": uuid4().hex,
            "endpoint": endpoint,
            # Keep the production RPC/readiness budget: a 0.3s override also
            # shrinks health freshness to 2s and rejects healthy busy runs.
        },
    )


class ForwardToP3(httpx.BaseTransport):
    """HTTP bridge only: no fabricated business responses or mocked Worker."""

    def __init__(self, client, execution):
        self.client = client
        self.execution = execution
        self.calls = []
        self.unavailable = []

    def handle_request(self, request):
        self.calls.append((request.method, request.url.path, request.headers.get("X-Operation-ID")))
        response = self.client.request(
            request.method,
            request.url.raw_path.decode(),
            headers=request.headers,
            content=request.content,
        )
        if response.status_code == 503:
            self.unavailable.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "response": response.json(),
                    "readiness": self.execution.ready(),
                    "worker": dict(self.execution.state),
                    "checked_age_seconds": time.monotonic() - self.execution.checked,
                }
            )
        return httpx.Response(
            response.status_code, headers=response.headers, content=response.content
        )


def until(call, condition, seconds=60):
    end = time.monotonic() + seconds
    value = None
    while time.monotonic() < end:
        value = call()
        if condition(value):
            return value
        time.sleep(0.05)
    raise AssertionError(f"convergence deadline exceeded: {value!r}")


@contextmanager
def real_chain(p3):
    with TestClient(p3.app()) as app:
        until(lambda: app.get("/p3/readyz").status_code, lambda value: value == 200)
        # Each migrated fixture has its own new Milvus collection. Provision it
        # through the real adapter before P4 asks for all dependency readiness;
        # health probes remain read-only and do not fabricate a ready backend.
        ctx = p3.runtime.foundation.identity.context("alice", timeout_seconds=300)
        app.portal.call(p3.runtime.vectors.prepare, ctx)
        bridge = ForwardToP3(app, p3.execution)
        demo = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
        server = ThreadingHTTPServer(("127.0.0.1", 0), P4Handler)
        server.demo_service = demo
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{server.server_port}",
                headers={"Origin": "http://127.0.0.1:5173"},
                trust_env=False,
            ) as browser:
                try:
                    yield browser, bridge
                except AssertionError as exc:
                    if bridge.unavailable:
                        exc.add_note(f"P3 dependency diagnostics: {bridge.unavailable!r}")
                    raise
        finally:
            server.shutdown()
            thread.join(2)
            server.server_close()
            demo.close()


def start(browser, run_id):
    return browser.post(
        PREFIX + "/runs", json={"scenario_id": "library-basic", "request_id": run_id}
    )


def business_writes(bridge):
    # Registration/checkpoints are durable bookkeeping, not story commands.
    return [
        call
        for call in bridge.calls
        if call[0] in {"POST", "PUT"} and not call[1].startswith("/p3/client-runs/")
    ]


def final(browser, run_id):
    value = until(
        lambda: browser.get(PREFIX + "/runs/" + run_id).json(),
        lambda value: value["state"] not in {"queued", "running"},
    )
    assert value["state"] == "passed", (
        value["state"],
        value["current_step"],
        value["error"],
        value["steps"][value["current_step"] - 1]["evidence"],
    )
    assert len(value["steps"]) == 6
    assert all(step["state"] == "passed" for step in value["steps"])
    assert value["mode"]["embedding"] == "injected"
    assert value["mode"]["scheduling"] == "temporal_v1"
    assert "机器学习入门" in value["steps"][5]["response_text"]
    saved = {
        ref["memory_id"]
        for step in value["steps"]
        if step["evidence"]["path"] == "/p3/remember"
        for ref in step["evidence"]["memories"]
    }
    assert len(saved) == 3
    for step in value["steps"]:
        evidence = step["evidence"]
        assert {ref["memory_id"] for ref in evidence["memories"]} <= saved
        assert all(check["passed"] for check in step["checks"])
        if evidence["path"] == "/p3/recall":
            assert evidence["context"]["rendered_context"] == step["response_text"]
        else:
            assert all(p["projection_state"] == "ready" for p in evidence["processing"])
    return value, saved


def test_real_six_turns_duplicate_start_and_new_scope(tmp_path, temporal_server):
    p3 = Service(configuration(tmp_path, temporal_server.endpoint))
    with real_chain(p3) as (browser, bridge):
        assert browser.get(PREFIX + "/scenarios").json()["enabled"] is True
        first_id = str(uuid4())
        assert start(browser, first_id).status_code == 202
        first, first_memories = final(browser, first_id)
        writes_before = business_writes(bridge)
        assert len(writes_before) == 6
        repeated = start(browser, first_id)
        assert repeated.status_code == 200
        assert repeated.json()["operations"] == first["operations"]
        assert repeated.json()["steps"] == first["steps"]
        assert business_writes(bridge) == writes_before
        second_id = str(uuid4())
        assert start(browser, second_id).status_code == 202
        second, second_memories = final(browser, second_id)
        assert first_memories.isdisjoint(second_memories)
        scope1 = first["steps"][0]["evidence"]["memories"][0]["scope"]
        scope2 = second["steps"][0]["evidence"]["memories"][0]["scope"]
        assert scope1["session_id"] != scope2["session_id"]
        assert scope1["task_id"] != scope2["task_id"]


def test_real_pending_job_observed_before_release_without_reposting(
    tmp_path, temporal_server, monkeypatch
):
    p3 = Service(configuration(tmp_path, temporal_server.endpoint))
    release = threading.Event()
    original = p3.runtime.remember.bodies.persist

    async def delayed(*args, **kwargs):
        while not release.is_set():
            await asyncio.sleep(0.02)
        return await original(*args, **kwargs)

    monkeypatch.setattr(p3.runtime.remember.bodies, "persist", delayed)
    with real_chain(p3) as (browser, bridge):
        run_id = str(uuid4())
        try:
            assert start(browser, run_id).status_code == 202
            pending = until(
                lambda: browser.get(PREFIX + "/runs/" + run_id).json(),
                lambda value: bool((value["steps"][0]["evidence"] or {}).get("job_id")),
            )
            evidence = pending["steps"][0]["evidence"]
            assert pending["state"] == "running"
            assert pending["steps"][0]["state"] == "running"
            assert pending["steps"][1]["state"] == "pending"
            assert not pending["steps"][0]["response_text"]
            job = evidence["job_id"]
            until(
                lambda: list(bridge.calls),
                lambda calls: any(path == f"/p3/operations/{job}" for _, path, _ in calls),
            )
            assert sum(path == "/p3/remember" for _, path, _ in bridge.calls) == 1
        finally:
            release.set()
        finished, _ = final(browser, run_id)
        assert finished["steps"][0]["evidence"]["job_id"] == job
        assert any(path == f"/p3/operations/{job}/result" for _, path, _ in bridge.calls)
        assert sum(path == "/p3/remember" for _, path, _ in bridge.calls) == 3
        commands = [op for _, _, op in business_writes(bridge)]
        assert len(commands) == len(set(commands)) == 6
        assert {item["operation_id"] for item in finished["operations"]} == set(commands)
        assert all(
            item["phase"] == "observed" and item["job_id"] for item in finished["operations"]
        )
        lookup = bridge.client.get(
            "/p3/operation-requests/" + commands[0],
            params={"kind": "remember.save"},
            headers={"Authorization": "Bearer alice"},
        )
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["state"] == "found" and lookup.json()["job_id"] == job
