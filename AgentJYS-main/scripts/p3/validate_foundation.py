"""Reproducible runtime checks, with raw execution artifacts kept in a temp directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--temporal-endpoint", default=os.environ.get("P3_TEMPORAL_ENDPOINT"))
    args = parser.parse_args()
    if not args.temporal_endpoint:
        parser.error("--temporal-endpoint (or P3_TEMPORAL_ENDPOINT) is required")
    target = "src/aether_agent_memory/runtime/foundation"
    with tempfile.TemporaryDirectory(prefix="p3-foundation-checks-") as tmp:
        junit = Path(tmp) / "runtime.xml"
        commands = [
            (
                "runtime_tests",
                [
                    "-m",
                    "pytest",
                    "tests/runtime/p3",
                    "-q",
                    "-o",
                    f"cache_dir={tmp}/pytest",
                    f"--junitxml={junit}",
                ],
            ),
            (
                "runtime_lint",
                [
                    "-m",
                    "ruff",
                    "check",
                    "--no-cache",
                    target,
                    "tests/runtime/p3",
                    "scripts/p3/demo_foundation.py",
                    "scripts/p3/validate_foundation.py",
                ],
            ),
            (
                "runtime_type_check",
                [
                    "-m",
                    "mypy",
                    "--config-file",
                    "scripts/p3/mypy.ini",
                    "--cache-dir",
                    f"{tmp}/mypy",
                    target,
                ],
            ),
            ("contract_regression", ["scripts/p3/validate_contracts.py"]),
            (
                "engineering_demo",
                [
                    "scripts/p3/demo_foundation.py",
                    "--directory",
                    f"{tmp}/demo",
                    "--temporal-endpoint",
                    args.temporal_endpoint,
                ],
            ),
        ]
        checks = []
        for name, command in commands:
            result = subprocess.run(
                [sys.executable, *command],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            print(f"[{name}] {'PASS' if result.returncode == 0 else 'FAIL'}")
            print(result.stdout.strip())
            if result.stderr:
                print(result.stderr.strip())
            checks.append(
                {"name": name, "passed": result.returncode == 0, "exit_code": result.returncode}
            )
        cases = []
        if junit.exists():
            for case in ET.parse(junit).iter("testcase"):
                cases.append(
                    {
                        "name": case.attrib["name"],
                        "passed": not any(
                            case.find(tag) is not None for tag in ("failure", "error", "skipped")
                        ),
                    }
                )
    passed = all(check["passed"] for check in checks)
    if args.report:
        artifacts = []
        for directory in (target, "tests/runtime/p3", "scripts/p3", "docs/p3/development"):
            for path in (ROOT / directory).rglob("*"):
                if (
                    path.is_file()
                    and path.suffix in {".py", ".md", ".txt", ".ini"}
                    and "__pycache__" not in path.parts
                ):
                    artifacts.append(path)
        artifacts += [
            ROOT / "contracts/p3/profiles/foundation.yaml",
            ROOT / "src/aether_agent_memory/runtime/capability_store.py",
        ]
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": "single_host_foundation_engineering_not_business_acceptance",
            "passed": passed,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "checks": checks,
            "runtime_cases": cases,
            "not_run": [
                "real_remember",
                "real_recall",
                "real_operate",
                "azure_deployment",
                "aks",
                "backup_restore",
                "otel_export",
                "production_metrics",
            ],
            "artifacts": [
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in sorted(set(artifacts))
            ],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
