"""A real, persistent Temporal server owned exclusively by this test session."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.gateway import connect_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(autouse=True)
def owned_azure_resources():
    from azure_test_runtime import OwnedResources, _resources

    resources = OwnedResources()
    token = _resources.set(resources)
    try:
        yield resources
    finally:
        try:
            resources.close()
        finally:
            _resources.reset(token)


class PersistentTemporal:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.binary = os.environ.get("P3_TEMPORAL_CLI") or shutil.which("temporal")
        if not self.binary:
            raise RuntimeError("set P3_TEMPORAL_CLI to the pinned Temporal CLI executable")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.endpoint = f"127.0.0.1:{self.port}"
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        assert self.binary is not None
        with (self.directory / "server.log").open("ab") as log:
            self.process = subprocess.Popen(
                [
                    self.binary,
                    "server",
                    "start-dev",
                    "--headless",
                    "--ip",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                    "--db-filename",
                    str(self.directory / "server.db"),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise RuntimeError((self.directory / "server.log").read_text(errors="replace"))
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        self.stop()
        raise TimeoutError("test-owned Temporal server did not start")

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None


@pytest.fixture(scope="session")
def temporal_server(tmp_path_factory):
    server = PersistentTemporal(tmp_path_factory.mktemp("temporal"))
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
async def temporal_env(temporal_server):
    return temporal_server


@pytest.fixture
async def workflow_client(temporal_env):
    return await connect_client(
        TemporalConfiguration(deployment_id="test", endpoint=temporal_env.endpoint)
    )


# Shared, explicitly opted-in Temporal test support.
sys.path.insert(0, str(Path(__file__).parent))


def pytest_collection_modifyitems(items):
    for item in items:
        if "temporal_server" in item.fixturenames:
            item.add_marker(pytest.mark.temporal_server)
