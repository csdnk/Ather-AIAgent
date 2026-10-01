"""Run mandatory real BGE + official Milvus Lite acceptance in an external directory.

PyCharm: select the prepared venv interpreter, this script, and parameters:
  --directory <external-work-directory> --embedding-config <native.json>
  --temporal-cli <temporal executable>
The runner creates a unique subdirectory and never reuses an existing basetemp.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

REQUIRED_TESTS = (
    "test_real_working_semantics_scope_and_source_topk",
    "test_real_working_database_and_service_restart",
    "test_real_working_share_correct_delete_and_expiry",
    "test_real_working_ambiguous_write_recovers_once",
    "test_real_working_late_projection_cannot_resurrect_deleted_memory",
)


def verify_junit(path: Path, returncode: int) -> dict:
    result = {"passed": False, "tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    try:
        root = ET.parse(path).getroot()
        cases = list(root.iter("testcase"))
    except (OSError, ET.ParseError) as exc:
        return {**result, "reason": str(exc)}
    counts = Counter(case.get("name") for case in cases)
    result.update(
        tests=len(cases),
        failures=sum(len(case.findall("failure")) for case in cases),
        errors=sum(len(case.findall("error")) for case in cases),
        skipped=sum(len(case.findall("skipped")) for case in cases),
    )
    result["missing_or_duplicate"] = [name for name in REQUIRED_TESTS if counts[name] != 1]
    result["passed"] = (
        returncode == 0
        and bool(cases)
        and not result["missing_or_duplicate"]
        and not any(result[key] for key in ("failures", "errors", "skipped"))
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--embedding-config", default=os.environ.get("P3_TEST_NATIVE_CONFIG"))
    parser.add_argument("--temporal-cli", default=os.environ.get("P3_TEMPORAL_CLI"))
    args = parser.parse_args(argv)
    for key in ("embedding_config", "temporal_cli"):
        value = getattr(args, key)
        if not value or not Path(value).is_file():
            parser.error(f"--{key.replace('_', '-')} must identify an existing file")
    root = Path(__file__).resolve().parents[2]
    repository = next((p for p in (root, *root.parents) if (p / ".git").exists()), root)
    directory = args.directory.expanduser().resolve()
    if directory == repository or repository in directory.parents:
        parser.error("--directory must be outside the source repository")
    directory.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="working-milvus-", dir=directory))
    env = os.environ.copy()
    env.update(
        P3_TEST_MILVUS_LITE="1",
        P3_TEST_NATIVE_CONFIG=str(Path(args.embedding_config).resolve()),
        P3_TEMPORAL_CLI=str(Path(args.temporal_cli).resolve()),
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTEST_ADDOPTS="",
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
        P3_MILVUS_EVIDENCE=str(run),
        PYTHONPATH=str(root / "src") + os.pathsep + str(root),
    )
    for key, relative in {
        "TEMP": "temp",
        "TMP": "temp",
        "XDG_CACHE_HOME": "cache",
        "HF_HOME": "cache/huggingface",
        "TIKTOKEN_CACHE_DIR": "cache/tiktoken",
    }.items():
        # A preseeded external tokenizer cache also supports an offline run.
        existing = env.get(key) if key == "TIKTOKEN_CACHE_DIR" else None
        target = Path(existing).resolve() if existing else run / relative
        if target == repository or repository in target.parents:
            parser.error(f"{key} must point outside the source repository")
        target.mkdir(parents=True, exist_ok=True)
        env[key] = str(target)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/integration/test_working_milvus.py",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(run / "pytest"),
        "--junitxml",
        str(run / "junit.xml"),
        "-v",
        "--tb=short",
    ]
    started = time.monotonic()
    print(f"Real BGE + Milvus Lite acceptance: {run}", flush=True)
    with (run / "pytest.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=root,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
    result = verify_junit(run / "junit.xml", completed.returncode)
    versions = {}
    for package in ("milvus-lite", "pymilvus", "fastembed", "onnxruntime", "temporalio", "pytest"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "missing"
    result.update(
        backend="official Milvus Lite gRPC",
        embedding="native BGE (no lexical fallback)",
        versions=versions,
        python=sys.version,
        command=command,
        elapsed_seconds=time.monotonic() - started,
        pytest_exit_code=completed.returncode,
        directory=str(run),
    )
    (run / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
