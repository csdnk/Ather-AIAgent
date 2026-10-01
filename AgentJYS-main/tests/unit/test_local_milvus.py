"""Local launcher must not silently relocate or corrupt a Unicode data directory."""

import importlib.util
import os
import sys

import pytest


def launcher():
    name = "aether_agent_memory.runtime.local_milvus"
    assert importlib.util.find_spec(name) is not None, "checked Lite launcher is missing"
    return __import__(name, fromlist=["prepare_data_directory"])


def test_prepared_directory_is_created_and_identical(tmp_path):
    directory = tmp_path / "milvus-真实数据-long-name"
    prepared = launcher().prepare_data_directory(directory)
    assert directory.is_dir()
    assert os.path.samefile(prepared, directory)
    if sys.platform == "win32":
        assert prepared.isascii()


def test_preparation_rejects_file(tmp_path):
    directory = tmp_path / "data"
    directory.write_text("user data")
    with pytest.raises((ValueError, FileExistsError)):
        launcher().prepare_data_directory(directory)
    assert directory.read_text() == "user data"


@pytest.mark.parametrize("bad_alias", ["non_ascii", "different_directory"])
def test_windows_alias_must_be_ascii_and_same_directory(tmp_path, monkeypatch, bad_alias):
    module = launcher()
    directory = tmp_path / "真实数据"
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(module.sys, "platform", "win32")
    alias = str(directory) if bad_alias == "non_ascii" else module.prepare_data_directory(other)
    monkeypatch.setattr(module, "_windows_short_path", lambda path: alias)
    with pytest.raises(ValueError, match="ASCII"):
        module.prepare_data_directory(directory)


def test_launcher_rejects_invalid_port_before_start(tmp_path):
    with pytest.raises(SystemExit) as error:
        launcher().main(["--directory", str(tmp_path / "data"), "--port", "0"])
    assert error.value.code == 2


def test_server_readiness_timeout_stops_owned_child(tmp_path, monkeypatch):
    name = "tests.integration.milvus_support"
    assert importlib.util.find_spec(name) is not None, "test-owned Lite lifecycle is missing"
    module = __import__(name, fromlist=["PersistentMilvus"])
    server = module.PersistentMilvus(tmp_path / "milvus")
    monkeypatch.setattr(
        server, "command", lambda: [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    spawned = []
    real_popen = module.subprocess.Popen

    def popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    with pytest.raises(TimeoutError):
        server.start(timeout=0.1)
    assert len(spawned) == 1
    assert spawned[0].poll() is not None
    assert server.process is None


def test_server_start_failure_leaves_no_child(tmp_path, monkeypatch):
    name = "tests.integration.milvus_support"
    assert importlib.util.find_spec(name) is not None, "test-owned Lite lifecycle is missing"
    module = __import__(name, fromlist=["PersistentMilvus"])
    server = module.PersistentMilvus(tmp_path / "milvus")
    monkeypatch.setattr(server, "command", lambda: [str(tmp_path / "missing-python")])
    with pytest.raises(OSError):
        server.start(timeout=0.1)
    assert server.process is None
