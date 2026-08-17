from __future__ import annotations

import csv
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "p3_baseline_v0.1"
SEED = 20260809


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, value: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    ensure_dir(path.parent)
    names = fieldnames or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    remainder = position - lower
    return ordered[lower] * (1.0 - remainder) + ordered[upper] * remainder


def latency_summary(values_ms: list[float]) -> dict[str, float | None]:
    return {
        "p50_ms": percentile(values_ms, 0.50),
        "p95_ms": percentile(values_ms, 0.95),
        "p99_ms": percentile(values_ms, 0.99),
    }


def safe_command(command: list[str], *, timeout: float = 10.0) -> dict[str, Any]:
    executable = shutil.which(command[0])
    if executable is None:
        return {"value": None, "reason": f"executable not found: {command[0]}"}
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return {"value": None, "reason": f"{type(exc).__name__}: {exc}"}
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        return {"value": None, "reason": output or f"exit code {completed.returncode}"}
    return {"value": output, "reason": None}


def package_info(name: str) -> dict[str, str | None]:
    try:
        return {"value": metadata.version(name), "reason": None}
    except metadata.PackageNotFoundError:
        return {"value": None, "reason": f"package not installed: {name}"}


def _redis_server_version() -> dict[str, str | None]:
    try:
        from redis import Redis

        client = Redis.from_url(
            os.getenv("AETHER_B2_REDIS_URL", "redis://127.0.0.1:6379/0"),
            socket_timeout=3,
        )
        try:
            return {"value": str(client.info("server")["redis_version"]), "reason": None}
        finally:
            client.close()
    except Exception as exc:
        return {"value": None, "reason": f"{type(exc).__name__}: {exc}"}


def _milvus_server_version() -> dict[str, str | None]:
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(
            uri=os.getenv("AETHER_B2_MILVUS_URI", "http://127.0.0.1:19530"),
            timeout=3,
        )
        try:
            return {"value": str(client.get_server_version()), "reason": None}
        finally:
            client.close()
    except Exception as exc:
        return {"value": None, "reason": f"{type(exc).__name__}: {exc}"}


def _cpu_model() -> dict[str, str | None]:
    if platform.system() == "Windows":
        result = safe_command(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)",
            ],
            timeout=15,
        )
        if result["value"]:
            return result
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return {"value": line.split(":", 1)[1].strip(), "reason": None}
    value = platform.processor().strip()
    return {"value": value or None, "reason": None if value else "CPU model unavailable"}


def _memory_bytes() -> dict[str, int | str | None]:
    try:
        import psutil

        return {"value": int(psutil.virtual_memory().total), "reason": None}
    except Exception:
        pass
    if platform.system() == "Windows":
        result = safe_command(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
            ],
            timeout=15,
        )
        try:
            return {"value": int(str(result["value"]).strip()), "reason": None}
        except (TypeError, ValueError):
            return {"value": None, "reason": str(result["reason"] or "RAM unavailable")}
    return {"value": None, "reason": "RAM unavailable"}


def collect_environment(parameters: dict[str, Any]) -> dict[str, Any]:
    if (REPO_ROOT / ".git").exists():
        git_commit = safe_command(["git", "rev-parse", "HEAD"])
        git_status = safe_command(["git", "status", "--short"])
    else:
        reason = "source supplied as a ZIP; this directory has no .git metadata"
        git_commit = {"value": None, "reason": reason}
        git_status = {"value": None, "reason": reason}
    physical_count: int | None = None
    physical_reason: str | None = None
    try:
        import psutil

        physical_count = psutil.cpu_count(logical=False)
    except Exception as exc:
        physical_reason = f"psutil unavailable: {exc}"
    return {
        "baseline_name": "P3 Engineering Baseline v0.1",
        "timestamp": utc_now(),
        "git_commit": git_commit,
        "git_status": git_status,
        "os": platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "cpu_model": _cpu_model(),
        "physical_cpu_count": {"value": physical_count, "reason": physical_reason},
        "logical_cpu_count": {"value": os.cpu_count(), "reason": None},
        "ram_bytes": _memory_bytes(),
        "docker_version": safe_command(["docker", "--version"]),
        "redis_server_version": _redis_server_version(),
        "redis_client_version": package_info("redis"),
        "milvus_server_version": _milvus_server_version(),
        "milvus_client_version": package_info("pymilvus"),
        "celery_version": package_info("celery"),
        "fastembed_version": package_info("fastembed"),
        "httpx_version": package_info("httpx"),
        "embedding_model": os.getenv("AETHER_B1_MODEL", "BAAI/bge-small-zh-v1.5"),
        "embedding_dimension": int(os.getenv("AETHER_B2_VECTOR_DIMENSION", "512")),
        "b1_threads": int(os.getenv("AETHER_B1_THREADS", "1")),
        "b1_max_concurrency": int(os.getenv("AETHER_B1_MAX_CONCURRENCY", "1")),
        "b1_model_batch_size": int(os.getenv("AETHER_B1_MODEL_BATCH_SIZE", "8")),
        "benchmark_parameters": parameters,
    }


def status_record(name: str, status: str, **details: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "timestamp": utc_now(), **details}


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))
