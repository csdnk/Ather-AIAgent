"""Bounded failure probe: test-owned P4 processes against real P3/Temporal/current P2."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually, headers

from azure_component_service import Service

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


@contextmanager
def p4_process(tmp_path, backend_port, port, name):
    credential = tmp_path / "demo-credential"
    credential.write_text("alice", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env.update(
        AETHER_P4_BIND="127.0.0.1",
        AETHER_P4_PORT=str(port),
        AETHER_P4_DEMO_ENABLED="1",
        AETHER_P4_DEMO_P3_URL=f"http://127.0.0.1:{backend_port}",
        AETHER_P4_DEMO_CREDENTIAL_FILE=str(credential),
        AETHER_P4_DEMO_ORIGINS='["http://127.0.0.1:5173"]',
    )
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with (tmp_path / (name + ".log")).open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "aether_p4_simulator.server"],
            env=env,
            cwd=ROOT,
            stdout=log,
            stderr=log,
            **flags,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=5,
                trust_env=False,
                headers={"Origin": "http://127.0.0.1:5173"},
            ) as browser:
                until = time.monotonic() + 20
                while time.monotonic() < until:
                    assert process.poll() is None, "owned P4 exited before binding"
                    try:
                        response = browser.get("/api/v1/demo/scenarios")
                        if response.status_code == 200 and response.json()["enabled"]:
                            break
                    except httpx.TransportError:
                        time.sleep(0.05)
                else:
                    raise AssertionError("P4 startup deadline")
                yield browser, process.pid
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def finish(browser, run_id):
    def check():
        response = browser.get("/api/v1/demo/runs/" + run_id)
        assert response.status_code == 200, response.text
        value = response.json()
        if value["state"] in ("queued", "running"):
            return None
        assert value["state"] == "passed", value
        return value

    return eventually(check)


def refs(run):
    return {
        ref["memory_id"]
        for step in run["steps"]
        if step["evidence"] and step["evidence"]["path"] == "/p3/remember"
        for ref in step["evidence"]["memories"]
    }


def restore_in_new_process(tmp_path, backend_port, run_id):
    """A fresh interpreter gets only the endpoint, credential file and original run ID."""
    script = tmp_path / "restore_execution.py"
    script.write_text(
        """import json, os, sys
from pathlib import Path
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.validation.client import P3ValidationClient

client = P3ValidationClient(sys.argv[1], Path(sys.argv[2]).read_text("utf-8"))
service = DemoService(client)
try:
    restored = service.restore_execution(sys.argv[3])
    print(json.dumps({
        "pid": os.getpid(),
        "run_id": str(restored.record.run_id),
        "head": restored.record.execution_state.model_dump(mode="json"),
        "operations": [item.model_dump(mode="json") for item in restored.envelope.operations],
        "data": restored.data.model_dump(mode="json"),
        "journal_matches": restored.journal_matches,
    }))
finally:
    service.close()
    client.close()
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    restored = subprocess.run(
        [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            str(script),
            f"http://127.0.0.1:{backend_port}",
            str(tmp_path / "demo-credential"),
            run_id,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        **flags,
    )
    assert restored.returncode == 0, restored.stderr
    return json.loads(restored.stdout)


def test_p4_process_restart_recovers_original_run_without_duplicate_writes(configuration, tmp_path):
    service = Service(configuration)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    backend_port = listener.getsockname()[1]
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        p4_port = reservation.getsockname()[1]
    app = service.app()
    calls = []

    @app.middleware("http")
    async def count_calls(request, call_next):
        calls.append((request.method, request.url.path))
        return await call_next(request)

    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="none"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    run_id = str(uuid4())
    request = {"scenario_id": "library-basic", "request_id": run_id}
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{backend_port}", timeout=5, trust_env=False
        ) as p3:

            def ready():
                try:
                    return p3.get("/p3/readyz").status_code == 200
                except httpx.TransportError:
                    return False

            eventually(ready)
            with p4_process(tmp_path, backend_port, p4_port, "before") as (browser, first_pid):
                accepted = browser.post("/api/v1/demo/runs", json=request)
                assert accepted.status_code == 202, accepted.text
                first = finish(browser, run_id)
            with p4_process(tmp_path, backend_port, p4_port, "after") as (browser, second_pid):
                recovered = browser.get("/api/v1/demo/runs/" + run_id)
                repeated = browser.post("/api/v1/demo/runs", json=request)
                assert repeated.status_code in (200, 202), repeated.text
                second = finish(browser, run_id)
            original_record = p3.get("/p3/client-runs/" + run_id, headers=headers()).json()
            before_restore = len(calls)
            execution = restore_in_new_process(tmp_path, backend_port, run_id)
            restore_calls = calls[before_restore:]
            assert restore_calls and all(method == "GET" for method, _ in restore_calls)
            assert p3.get("/p3/client-runs/" + run_id, headers=headers()).json() == original_record
            assert execution["pid"] not in {os.getpid(), first_pid, second_pid}
            assert execution["run_id"] == run_id and execution["journal_matches"]
            assert execution["head"] == original_record["execution_state"]
            assert execution["operations"] == first["operations"]
            data = execution["data"]
            assert set(data["receipts"]) == {"rules", "loan", "noise"}
            assert data["completed_steps"] == [1, 2, 3, 4, 5, 6]
            assert set(data["refs"]) == refs(first)
            assert all(result["consumed"] for result in data["resolved"].values())
            first_jobs = [
                s["evidence"]["job_id"] for s in first["steps"] if s["evidence"]["job_id"]
            ]
            assert first_jobs, "probe must preserve actual original Temporal job receipts"
            old_results = [
                p3.get("/p3/operations/" + job + "/result", headers=headers()).status_code
                for job in first_jobs
            ]
            record = {
                "run_id": run_id,
                "first_pid": first_pid,
                "second_pid": second_pid,
                "first_state": first["state"],
                "second_state": second["state"],
                "original_run_lookup_status": recovered.status_code,
                "original_run_lookup": recovered.json(),
                "same_uuid_post_status": repeated.status_code,
                "first_memory_ids": sorted(refs(first)),
                "second_memory_ids": sorted(refs(second)),
                "first_job_ids": first_jobs,
                "first_job_result_statuses": old_results,
                "first_operations": [s["evidence"]["operation_id"] for s in first["steps"]],
                "second_operations": [s["evidence"]["operation_id"] for s in second["steps"]],
                "first_mode": first["mode"],
                "second_mode": second["mode"],
                "first_journal": first["operations"],
                "second_journal": second["operations"],
                "scope_before": first["steps"][0]["evidence"]["memories"][0]["scope"],
                "scope_after": second["steps"][0]["evidence"]["memories"][0]["scope"],
                "restored_execution": execution,
                "restore_calls": restore_calls,
                "boundary": (
                    "P4 actual process restart; P3/Temporal/current P2 retained; "
                    "reference metadata/lexical processing"
                ),
            }
            with (tmp_path / "process-recovery.json").open("x", encoding="utf-8") as evidence:
                evidence.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            assert all(status == 200 for status in old_results)
            assert len(first["operations"]) == 6
            assert all(
                item["phase"] == "observed" and item["job_id"] for item in first["operations"]
            )
            assert first["operations"] == second["operations"]
            assert first["steps"] == second["steps"]
            assert (
                recovered.status_code == 200
                and repeated.status_code == 200
                and refs(first) == refs(second)
            ), (
                "P4 lost its completed run and accepted the same UUID as a new run",
                recovered.status_code,
                repeated.status_code,
                len(refs(first)),
                len(refs(second)),
            )
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        listener.close()
        assert not thread.is_alive(), "owned P3 must stop"
