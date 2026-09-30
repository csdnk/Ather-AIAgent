"""Kill only child PIDs created here, at recorded durable business boundaries."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tests/runtime/temporal/process_worker.py"


def wait_file(path, process, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists():
            return json.loads(path.read_text("utf-8"))
        assert process.poll() is None, (path, process.returncode)
        time.sleep(0.05)
    raise AssertionError(f"no precise rendezvous: {path}")


@pytest.mark.parametrize("scenario", ["T02", "T04", "T05", "T07", "T09", "T10"])
def test_process_recovery(tmp_path, temporal_server, scenario):
    assert HELPER.is_file(), "missing real P3 process recovery harness"
    processes = []

    def start(phase):
        with (tmp_path / f"{phase}.log").open("wb") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(HELPER),
                    "--directory",
                    str(tmp_path),
                    "--endpoint",
                    temporal_server.endpoint,
                    "--scenario",
                    scenario,
                    "--phase",
                    phase,
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        processes.append(child)
        return child

    try:
        first = start("interrupt")
        before = wait_file(tmp_path / "rendezvous.json", first)
        assert before["scenario"] == scenario
        first.kill()
        first.wait(timeout=10)
        if scenario == "T10":
            temporal_server.stop()
            temporal_server.start()
        second = start("resume")
        after = wait_file(tmp_path / "result.json", second)
        assert second.wait(timeout=60) == 0
        assert after["job_id"] == before["job_id"]
        assert after["input_hash"] == before["input_hash"]
        assert after["business_effect_count"] == 1
        assert after["result_ref"] == before["result_ref"]
        assert after["state"] == "succeeded"
        if before.get("first_run_id"):
            assert after["first_run_id"] == before["first_run_id"]
        if scenario == "T04":
            assert before["checkpoint_count"] >= 1
            assert after["reused_checkpoint_hash"] == before["checkpoint_hash"]
            assert after["first_chunk_calls"] == 1
        if scenario == "T07":
            assert after["late_rejection"] == "VERSION_CONFLICT"
        if scenario == "T09":
            assert after["inbox_signature"] == before["inbox_signature"]
            assert after["delivery_state"] == "acknowledged"
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
