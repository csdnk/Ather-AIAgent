"""Test-only ownership of an independent official Lite process and evidence."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


class PersistentMilvus:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.uri = f"http://127.0.0.1:{self.port}"
        self.process = None
        self.history = []

    def command(self):
        return [
            sys.executable,
            "-m",
            "aether_agent_memory.runtime.local_milvus",
            "--directory",
            str(self.directory / "data"),
            "--port",
            str(self.port),
        ]

    def start(self, timeout=60):
        if self.process is not None:
            raise RuntimeError("owned Milvus process already started")
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            with (self.directory / "server.log").open("ab") as log:
                self.process = subprocess.Popen(
                    self.command(),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            self.history.append({"pid": self.process.pid, "port": self.port})
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if self.process.poll() is not None:
                    raise RuntimeError((self.directory / "server.log").read_text(errors="replace"))
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                        from pymilvus import MilvusClient

                        probe = MilvusClient(uri=self.uri, timeout=1)
                        try:
                            probe.list_collections(timeout=1)
                            return
                        finally:
                            probe.close()
                except Exception:
                    time.sleep(0.05)
            raise TimeoutError("test-owned official Milvus Lite did not become ready")
        except BaseException:
            self.stop()
            raise

    def stop(self):
        child = self.process
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            self.history[-1]["exit_code"] = child.returncode
            self.process = None
        if self.directory.is_dir():
            (self.directory / "processes.json").write_text(
                json.dumps(self.history, indent=2), encoding="utf-8"
            )


def evidence(name, value):
    directory = os.environ.get("P3_MILVUS_EVIDENCE")
    if directory:
        (Path(directory) / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
