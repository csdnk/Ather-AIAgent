"""Pinned, persistent local Temporal. Only a token-authenticated owner stops its child.

Development use only: the loopback server has no production authentication.
The owner protocol avoids terminating an unrelated process after PID reuse.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from aether_agent_memory.runtime.temporal.locking import DirectoryLock  # noqa: E402

CLI_VERSION = "1.9.1"


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), "utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def metadata(directory):
    path = directory / "owner.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def request(directory, command):
    saved = metadata(directory)
    if not saved.get("control_port"):
        return None
    try:
        with socket.create_connection(("127.0.0.1", saved["control_port"]), timeout=2) as conn:
            conn.sendall(json.dumps({"token": saved["owner_token"], "command": command}).encode())
            value = json.loads(conn.recv(16384))
            if value.get("owner_token") == saved["owner_token"]:
                return value
    except (OSError, ValueError):
        pass
    return None


def status(directory):
    directory = Path(directory).resolve()
    return request(directory, "status") or {"state": "stopped", "ready": False}


def verified_binary(binary):
    binary = Path(binary).resolve()
    try:
        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Temporal CLI executable required") from exc
    if not version.startswith(f"temporal version {CLI_VERSION} "):
        raise ValueError(f"Temporal CLI {CLI_VERSION} required; got {version}")
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def start(directory, binary):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    binary, digest = verified_binary(binary)
    lock = DirectoryLock()
    lock.acquire(directory / "command-lock")
    try:
        existing = status(directory)
        if existing["state"] == "running":
            if existing["binary_sha256"] != digest:
                raise ValueError("running Temporal CLI differs")
            return existing
        token = secrets.token_hex(32)
        with (directory / "owner.log").open("ab") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "_owner",
                    "--directory",
                    str(directory),
                    "--binary",
                    str(binary),
                    "--token",
                    token,
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        end = time.monotonic() + 45
        while time.monotonic() < end:
            current = status(directory)
            if current.get("owner_token") == token and current["ready"]:
                return current
            if child.poll() is not None:
                raise RuntimeError(f"Temporal owner failed; inspect {directory / 'owner.log'}")
            time.sleep(0.1)
        # Ask the owner to clean up; never target a PID from persisted metadata.
        request(directory, "stop")
        raise TimeoutError("Temporal development server did not become healthy")
    finally:
        lock.release()


def stop(directory):
    directory = Path(directory).resolve()
    lock = DirectoryLock()
    lock.acquire(directory / "command-lock")
    try:
        if request(directory, "stop") is None:
            return {"state": "stopped", "ready": False}
        end = time.monotonic() + 20
        while time.monotonic() < end:
            if status(directory)["state"] == "stopped":
                return {"state": "stopped", "ready": False}
            time.sleep(0.1)
        raise TimeoutError("Temporal owner did not stop")
    finally:
        lock.release()


async def healthy(endpoint):
    from temporalio.client import Client

    try:
        client = await asyncio.wait_for(Client.connect(endpoint), 1)
        return await client.service_client.check_health(timeout=timedelta(seconds=1))
    except Exception:
        return False


def owner(directory, binary, token):
    lock = DirectoryLock()
    lock.acquire(directory / "server-lock")
    child = None
    try:
        binary, digest = verified_binary(binary)
        old = metadata(directory)
        port = int(old["endpoint"].rsplit(":", 1)[1]) if old.get("endpoint") else 0
        if not port:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        with socket.socket() as control, (directory / "temporal.log").open("ab") as log:
            control.bind(("127.0.0.1", 0))
            control.listen(8)
            control.settimeout(0.2)
            child = subprocess.Popen(
                [
                    str(binary),
                    "server",
                    "start-dev",
                    "--headless",
                    "--ip",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--db-filename",
                    str(directory / "temporal.db"),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            value = {
                "state": "running",
                "ready": False,
                "pid": os.getpid(),
                "child_pid": child.pid,
                "control_port": control.getsockname()[1],
                "owner_token": token,
                "endpoint": f"127.0.0.1:{port}",
                "cli_version": CLI_VERSION,
                "binary_sha256": digest,
            }
            write(directory / "owner.json", value)
            next_health = 0.0
            while child.poll() is None:
                if time.monotonic() >= next_health:
                    value["ready"] = asyncio.run(healthy(value["endpoint"]))
                    next_health = time.monotonic() + 1
                try:
                    conn, _ = control.accept()
                except TimeoutError:
                    continue
                with conn:
                    conn.settimeout(2)
                    try:
                        msg = json.loads(conn.recv(4096))
                        if not secrets.compare_digest(str(msg.get("token", "")), token):
                            continue
                        conn.sendall(json.dumps(value).encode())
                        if msg.get("command") == "stop":
                            break
                    except (OSError, ValueError):
                        continue
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status", "_owner"])
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--token", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.action in {"start", "_owner"} and not args.binary:
        parser.error("--binary is required for start")
    if args.action == "_owner":
        owner(args.directory.resolve(), args.binary, args.token)
        return
    result = (
        start(args.directory, args.binary)
        if args.action == "start"
        else (stop(args.directory) if args.action == "stop" else status(args.directory))
    )
    print(json.dumps({k: v for k, v in result.items() if k != "owner_token"}))


if __name__ == "__main__":
    main()
