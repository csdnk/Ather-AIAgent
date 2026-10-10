"""Security and lifecycle tests for the local Apifox benchmark adapter."""

import importlib.util
import json
import threading
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[2] / "scripts/quality/benchmark_bridge.py"
    assert path.exists(), "Apifox benchmark bridge is required"
    spec = importlib.util.spec_from_file_location("benchmark_bridge", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_auth_origin_host_and_body_are_fail_closed():
    m = module()
    assert m.authorized("127.0.0.1:14884", "https://app.apifox.com", "Bearer abc", "abc")
    assert not m.authorized("evil.example:14884", None, "Bearer abc", "abc")
    assert not m.authorized("127.0.0.1:14884", "https://evil.example", "Bearer abc", "abc")
    assert not m.authorized("127.0.0.1:14884", None, "", "abc")
    with pytest.raises(ValueError):
        m.validate({"profile": "embedding", "request_id": "a", "command": "whoami"})
    with pytest.raises(ValueError):
        m.validate({"profile": "../../x", "request_id": "a"})


def test_idempotency_concurrency_and_budget(tmp_path):
    m = module()
    release = threading.Event()

    def execute(profile, jobdir):
        release.wait(2)
        return {"status": "PASS", "run_id": "real-component-run", "metrics": {"completed_valid": 2}}

    manager = m.Manager(tmp_path, execute, max_jobs=1)
    first = manager.submit({"profile": "embedding", "request_id": "one"})
    assert (
        manager.submit({"profile": "embedding", "request_id": "one"})["job_id"] == first["job_id"]
    )
    with pytest.raises(m.Conflict):
        manager.submit({"profile": "compression", "request_id": "one"})
    with pytest.raises(m.Conflict):
        manager.submit({"profile": "embedding", "request_id": "two"})
    release.set()
    job = manager.wait(first["job_id"], 3)
    assert job["status"] == "PASS" and job["report"]["run_id"] == "real-component-run"
    assert job["report"]["release_gate"] == "BLOCKED"
    with pytest.raises(m.Conflict):
        manager.submit({"profile": "embedding", "request_id": "two"})


def test_blocked_failure_and_restart_preserve_evidence(tmp_path):
    m = module()

    def blocked(profile, jobdir):
        return {"status": "BLOCKED", "reason": "MODEL_ASSET_UNAVAILABLE"}

    manager = m.Manager(tmp_path, blocked)
    job = manager.submit({"profile": "compression", "request_id": "one"})
    done = manager.wait(job["job_id"], 2)
    assert done["status"] == "BLOCKED"
    restarted = m.Manager(tmp_path, blocked)
    assert restarted.submit({"profile": "compression", "request_id": "one"})["status"] == "BLOCKED"
    state = json.loads((tmp_path / job["job_id"] / "job.json").read_text())
    state["status"] = "RUNNING"
    (tmp_path / job["job_id"] / "job.json").write_text(json.dumps(state))
    interrupted = m.Manager(tmp_path, blocked).wait(job["job_id"], 0)
    assert interrupted["status"] == "FAIL"
    assert interrupted["report"]["reason"] == "RUNNER_RESTARTED"
    with pytest.raises(m.Conflict):
        m.Manager(tmp_path, blocked).submit({"profile": "embedding", "request_id": "after-restart"})


def test_empty_success_report_is_not_success(tmp_path):
    m = module()
    manager = m.Manager(tmp_path, lambda *_: {"status": "PASS"})
    job = manager.submit({"profile": "embedding", "request_id": "one"})
    assert manager.wait(job["job_id"], 2)["status"] == "FAIL"


def test_uncertain_create_cleanup_checks_full_identity_and_uid(tmp_path):
    path = Path(__file__).parents[2] / "scripts/quality/benchmark_cloud_adapter.py"
    spec = importlib.util.spec_from_file_location("benchmark_cloud_adapter", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    calls = []

    def call(args, data=None):
        calls.append((args, data))
        if args[-1] == "json":
            return json.dumps(
                {
                    "metadata": {
                        "uid": "unique-pod-uid",
                        "labels": {"owner": m.OWNER, "benchmark-job": tmp_path.name},
                    }
                }
            ).encode()
        return b""

    m.cleanup(call, tmp_path)
    delete = next((args, data) for args, data in calls if args[0] == "delete")
    assert json.loads(delete[1])["preconditions"]["uid"] == "unique-pod-uid"
    assert json.loads((tmp_path / "cleanup.json").read_text())["deleted"]

    def foreign(args, data=None):
        return json.dumps(
            {
                "metadata": {
                    "uid": "foreign",
                    "labels": {"owner": m.OWNER, "benchmark-job": "another-job"},
                }
            }
        ).encode()

    with pytest.raises(RuntimeError, match="ownership"):
        m.cleanup(foreign, tmp_path)


def test_native_component_scene_requires_real_api_ids_and_gate_object():
    import sys

    sys.path.insert(0, str(Path(__file__).parents[2] / "scripts/quality"))
    import apifox_component as a

    routes = ["/v1/capabilities", "/v1/jobs", "/v1/jobs/{job_id}", "/v1/jobs/{job_id}/report"]
    source = {
        "apifoxProject": 1,
        "$schema": "test",
        "moduleSettings": [{"id": a.MODULE}],
        "environments": [],
        "apiTestCaseCollection": [],
        "apiCollection": [
            {
                "items": [
                    {"api": {"id": n + 1, "moduleId": a.MODULE, "path": p}}
                    for n, p in enumerate(routes)
                ]
            }
        ],
    }
    package = a.build(source)
    scenes = package["apiTestCaseCollection"][0]["items"]
    assert all(len(s["steps"]) == 44 for s in scenes)
    assert all(step["httpApiCase"]["apiId"] in [1, 2, 3, 4] for s in scenes for step in s["steps"])
    schema = package["schemaCollection"][0]["items"][1]["schema"]["jsonSchema"]
    assert schema["properties"]["report"]["properties"]["component_gate"]["type"] == "object"


def test_apifox_browser_preflight_allows_its_actual_headers(tmp_path):
    import http.client
    from http.server import ThreadingHTTPServer

    m = module()
    manager = m.Manager(tmp_path, lambda *_: {"status": "BLOCKED"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), m.handler(manager, "a" * 32))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "OPTIONS",
            "/v1/jobs",
            headers={
                "Host": "127.0.0.1:14884",
                "Origin": m.ORIGIN,
                "Access-Control-Request-Headers": "authorization,cache-control,content-type",
            },
        )
        response = connection.getresponse()
        assert response.status == 204
        allowed = {
            x.strip().lower() for x in response.getheader("Access-Control-Allow-Headers").split(",")
        }
        assert {"authorization", "cache-control", "content-type"} <= allowed
        assert response.getheader("Access-Control-Allow-Origin") == m.ORIGIN
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_forced_timeout_keeps_admission_closed_after_absence_check(tmp_path, monkeypatch):
    import subprocess
    import types

    m = module()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, 600)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(m.subprocess, "run", run)
    report = m.executor(
        {"python": "python", "adapter": "adapter.py", "adapter_config": "config.json"}
    )("embedding", tmp_path)
    assert report["status"] == "FAIL" and report["cleanup_pending"] is True
    assert report["cleanup_attempt_succeeded"] is True
    assert calls[1][-1] == "--cleanup-only"


def test_trusted_bridge_profiles_do_not_accept_client_supplied_commands(tmp_path):
    m = module()
    manager = m.Manager(
        tmp_path,
        lambda *_: {"status": "BLOCKED", "reason": "required stage not run"},
        profiles=("deployment-regression",),
        max_jobs=1,
    )
    with pytest.raises(ValueError):
        manager.submit({"profile": "embedding", "request_id": "x"})
    with pytest.raises(ValueError):
        manager.submit({"profile": "deployment-regression", "request_id": "x", "command": "x"})
    job = manager.submit({"profile": "deployment-regression", "request_id": "one"})
    assert manager.wait(job["job_id"], 2)["status"] == "BLOCKED"
