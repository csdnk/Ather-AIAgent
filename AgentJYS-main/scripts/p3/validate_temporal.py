"""Evidence-layered Temporal checks; failed or missing scenarios return nonzero."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROCESS_SCENARIOS = {"T02", "T04", "T05", "T07", "T09", "T10"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--level", choices=["unit", "server", "process"], required=True)
    args = parser.parse_args()
    # Keep evidence paths short enough for Windows immutable-body temp names.
    output = args.directory.resolve() / uuid.uuid4().hex[:8]
    output.mkdir(parents=True)
    xml = output / "results.xml"
    targets = (
        ["tests/integration/test_temporal_process_recovery.py"]
        if args.level == "process"
        else ["tests/runtime/temporal"]
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-q",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(output / "t"),
        f"--junitxml={xml}",
    ]
    if args.level == "unit":
        command += ["-m", "not temporal_server"]
    binary = os.environ.get("P3_TEMPORAL_CLI")
    version, binary_hash = None, None
    if args.level != "unit":
        if not binary:
            parser.error("set P3_TEMPORAL_CLI to the pinned Temporal CLI")
        from temporal_dev import verified_binary

        _, binary_hash = verified_binary(Path(binary))
        version = "1.9.1"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
    with (output / "pytest.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    cases = []
    if xml.exists():
        for case in ET.parse(xml).iter("testcase"):
            status = (
                "failed"
                if any(case.find(tag) is not None for tag in ("failure", "error"))
                else ("skipped" if case.find("skipped") is not None else "passed")
            )
            cases.append({"name": case.attrib["name"], "status": status})
    covered = {
        s
        for s in PROCESS_SCENARIOS
        if any(f"[{s}]" in c["name"] and c["status"] == "passed" for c in cases)
    }
    passed = result.returncode == 0 and bool(cases) and all(c["status"] == "passed" for c in cases)
    if args.level == "process":
        passed &= covered == PROCESS_SCENARIOS
    files = sorted(
        p
        for base in (ROOT / "src", ROOT / "tests", ROOT / "scripts/p3")
        for p in base.rglob("*.py")
    )
    artifacts = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    artifacts["pyproject.toml"] = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "level": args.level,
        "passed": bool(passed),
        "sdk": importlib.metadata.version("temporalio"),
        "cli_version": version,
        "binary_sha256": binary_hash,
        "cases": cases,
        "process_scenarios": sorted(covered),
        "source_hashes": artifacts,
        "raw_evidence": str(output),
        "not_verified": [
            "azure_aks",
            "external_p2",
            "database_recovery",
            "live_models",
            "live_milvus",
        ],
        "command": command,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), "utf-8")
    print(
        json.dumps(
            {
                "level": args.level,
                "passed": bool(passed),
                "cases": len(cases),
                "report": str(args.report),
                "process_scenarios": sorted(covered),
            }
        )
    )
    if not passed:
        print(
            "\n".join(
                (output / "pytest.log").read_text("utf-8", errors="replace").splitlines()[-45:]
            )
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
