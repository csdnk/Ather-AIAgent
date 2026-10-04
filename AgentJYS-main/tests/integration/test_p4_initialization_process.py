"""Kill a test-owned P4 process before first state; a new interpreter resumes the original run."""

import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually, headers

from azure_component_service import Service

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def stop_owned(process, actual_pid=None):
    """Stop only this test's launcher tree and verify its actual interpreter exited."""
    kernel, handle = None, None
    if os.name == "nt" and actual_pid is not None:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, actual_pid)
        assert handle, "actual test interpreter must be alive before termination"
    try:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=True,
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                process.terminate()
        process.wait(timeout=10)
        if handle is not None:
            assert kernel.WaitForSingleObject(handle, 10000) == 0, "actual old P4 did not exit"
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


def test_new_process_resumes_original_initialization_after_old_process_is_killed(
    configuration, tmp_path
):
    service = Service(configuration)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"
    app = service.app()
    calls = []

    @app.middleware("http")
    async def observe(request, call_next):
        calls.append((request.method, request.url.path, request.headers.get("X-Operation-ID")))
        return await call_next(request)

    script = tmp_path / "initialization_process.py"
    script.write_text(
        """import json, os, sys, time
from pathlib import Path
from uuid import UUID
from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.validation.client import P3ValidationClient

client = P3ValidationClient(sys.argv[1], Path(sys.argv[2]).read_text("utf-8"))
demo = DemoService(client)
try:
    if sys.argv[4] == "before":
        def pause_before_state(record, payload):
            marker = {"pid": os.getpid(), "run_id": str(record.run_id)}
            Path(sys.argv[5]).write_text(json.dumps(marker), encoding="utf-8")
            while True:
                time.sleep(0.1)
        demo._registry.save_state = pause_before_state
        demo.start(StartRequest(scenario_id="library-basic", request_id=UUID(sys.argv[3])))
    else:
        demo.recover_initialization(sys.argv[3], "process-init-recovery")
        demo.recover_initialization(sys.argv[3], "process-init-recovery")
finally:
    demo.close()
print(json.dumps({"pid": os.getpid(), "owner_id": demo._owner_id, "run_id": sys.argv[3]}))
""",
        encoding="utf-8",
    )
    credential = tmp_path / "credential"
    credential.write_text("alice", encoding="utf-8")
    marker = tmp_path / "before-first-state.json"
    run_id = str(uuid4())
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="none"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    first = None
    try:
        with httpx.Client(base_url=endpoint, timeout=15, trust_env=False) as p3:

            def ready():
                try:
                    return p3.get("/p3/readyz").status_code == 200
                except httpx.TransportError:
                    return False

            eventually(ready)
            with (tmp_path / "before.log").open("wb") as log:
                first = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-X",
                        "utf8",
                        str(script),
                        endpoint,
                        str(credential),
                        run_id,
                        "before",
                        str(marker),
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    **flags,
                )

                def paused():
                    assert first.poll() is None, (
                        "test-owned original P4 exited before the interruption"
                    )
                    return json.loads(marker.read_text("utf-8")) if marker.exists() else None

                before = eventually(paused)
                assert before["run_id"] == run_id and before["pid"] != os.getpid()
                if os.name != "nt":
                    assert before["pid"] == first.pid
                stop_owned(first, before["pid"])
            original = p3.get(f"/p3/client-runs/{run_id}", headers=headers()).json()
            assert original["snapshot"]["state"] == "running"
            assert original["execution_state"] is None and original["snapshot"]["operations"] == []
            original_bytes = p3.get(
                f"/p3/client-runs/{run_id}/definition", headers=headers()
            ).content
            assert not [
                row
                for row in calls
                if row[0] in {"POST", "PUT"} and not row[1].startswith("/p3/client-runs/")
            ]
            resumed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-X",
                    "utf8",
                    str(script),
                    endpoint,
                    str(credential),
                    run_id,
                    "after",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=150,
                **flags,
            )
            assert resumed.returncode == 0, resumed.stdout + resumed.stderr
            after = json.loads(resumed.stdout)
            assert after["pid"] not in {os.getpid(), before["pid"]}
            final = p3.get(f"/p3/client-runs/{run_id}", headers=headers()).json()
            assert final["snapshot"]["state"] == "passed", final["snapshot"]
            assert (
                final["owner_id"] == after["owner_id"] and final["owner_id"] != original["owner_id"]
            )
            assert (
                final["scope_id"] == original["scope_id"]
                and final["definition"] == original["definition"]
            )
            assert (
                p3.get(f"/p3/client-runs/{run_id}/definition", headers=headers()).content
                == original_bytes
            )
            effects = [
                row
                for row in calls
                if row[0] in {"POST", "PUT"} and not row[1].startswith("/p3/client-runs/")
            ]
            assert effects and len({row[2] for row in effects}) == len(effects)
            jobs = [item["job_id"] for item in final["snapshot"]["operations"] if item["job_id"]]
            assert jobs
            statuses = [
                p3.get(f"/p3/operations/{job}/result", headers=headers()).status_code
                for job in jobs
            ]
            assert all(status == 200 for status in statuses)
            (tmp_path / "initialization-process-evidence.json").write_text(
                json.dumps(
                    {
                        "before": before,
                        "after": after,
                        "original_record": original,
                        "final_record": final,
                        "business_calls": effects,
                        "original_jobs": jobs,
                        "original_result_statuses": statuses,
                        "boundary": (
                            "Actual terminated P4 process and new interpreter; "
                            "real P3 TCP/current P2, test Temporal and reference metadata/cache."
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        if first is not None and first.poll() is None:
            stop_owned(first)
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "test-owned P3 server did not stop"
