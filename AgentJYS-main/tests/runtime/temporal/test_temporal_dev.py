import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def launcher():
    path = ROOT / "scripts/p3/temporal_dev.py"
    assert path.is_file(), "persistent development server manager is missing"
    spec = importlib.util.spec_from_file_location("temporal_dev", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.temporal_server
def test_owned_server_restarts_with_same_store(tmp_path):
    manager = launcher()
    binary = Path(os.environ["P3_TEMPORAL_CLI"])
    try:
        first = manager.start(tmp_path, binary)
        assert first["ready"] and first["cli_version"] == "1.9.1"
        assert manager.start(tmp_path, binary)["owner_token"] == first["owner_token"]
        assert manager.status(tmp_path)["ready"]
        assert manager.stop(tmp_path)["state"] == "stopped"
        assert (tmp_path / "temporal.db").is_file()
        second = manager.start(tmp_path, binary)
        assert second["endpoint"] == first["endpoint"]
        assert second["owner_token"] != first["owner_token"]
    finally:
        manager.stop(tmp_path)


def test_stale_metadata_cannot_target_unowned_process(tmp_path):
    import json

    manager = launcher()
    (tmp_path / "owner.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "control_port": 1,
                "owner_token": "stale",
                "endpoint": "127.0.0.1:1",
            }
        ),
        "utf-8",
    )
    assert manager.stop(tmp_path)["state"] == "stopped"
    assert os.getpid() > 0


def test_wrong_cli_is_rejected_before_launch(tmp_path):
    manager = launcher()
    with pytest.raises(ValueError, match="CLI"):
        manager.start(tmp_path, Path(os.__file__))


def test_stop_waits_for_owner_to_release_server_lock(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout
    from threading import Event

    from aether_agent_memory.runtime.temporal.locking import DirectoryLock

    manager = launcher()
    lock = DirectoryLock()
    lock.acquire(tmp_path / "server-lock")
    observed = Event()

    def request(directory, command):
        if command == "stop":
            return {"state": "running", "ready": True}
        observed.set()
        return None

    monkeypatch.setattr(manager, "request", request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.stop, tmp_path)
        try:
            assert observed.wait(2)
            with pytest.raises(FutureTimeout):
                future.result(timeout=0.05)
        finally:
            lock.release()
        assert future.result(timeout=3)["state"] == "stopped"
