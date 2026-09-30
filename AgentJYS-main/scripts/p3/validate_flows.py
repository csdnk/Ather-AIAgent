"""Three-flow tests and full foundation/contract regressions; no live model keys required."""

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
TARGETS = [
    "src/aether_agent_memory/runtime/vector_backend.py",
    "src/aether_agent_memory/remember/basic",
    "src/aether_agent_memory/recall/basic",
    "src/aether_agent_memory/operate/basic",
    "src/aether_agent_memory/runtime/flows",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--temporal-endpoint", default=os.environ.get("P3_TEMPORAL_ENDPOINT"))
    args = parser.parse_args()
    if not args.temporal_endpoint:
        parser.error("--temporal-endpoint (or P3_TEMPORAL_ENDPOINT) is required")
    checks = []
    with tempfile.TemporaryDirectory(prefix="p3-three-flows-") as tmp:
        xml = Path(tmp) / "flows.xml"
        commands = [
            (
                "flow_tests",
                [
                    "-m",
                    "pytest",
                    "tests/runtime/flows",
                    "tests/integration/test_continuous_service.py",
                    "tests/integration/test_monitor_catalogs.py",
                    "tests/unit/test_service_model_provider.py",
                    "-q",
                    "-o",
                    f"cache_dir={tmp}/pytest",
                    f"--junitxml={xml}",
                ],
            ),
            (
                "flow_lint",
                [
                    "-m",
                    "ruff",
                    "check",
                    "--no-cache",
                    *TARGETS,
                    "tests/runtime/flows",
                    "scripts/p3/demo_flows.py",
                    "scripts/p3/validate_flows.py",
                    "scripts/p3/validate_recall.py",
                ],
            ),
            (
                "flow_types",
                [
                    "-m",
                    "mypy",
                    "--config-file",
                    "scripts/p3/mypy.ini",
                    "--cache-dir",
                    f"{tmp}/mypy",
                    *TARGETS,
                ],
            ),
            (
                "foundation_and_contract_regression",
                [
                    "scripts/p3/validate_foundation.py",
                    "--temporal-endpoint",
                    args.temporal_endpoint,
                ],
            ),
            (
                "three_flow_demo",
                [
                    "scripts/p3/demo_flows.py",
                    "--temporal-endpoint",
                    args.temporal_endpoint,
                    "--embedding-profile",
                    "lexical",
                    "--directory",
                    f"{tmp}/demo",
                ],
            ),
        ]
        for name, arguments in commands:
            completed = subprocess.run(
                [sys.executable, *arguments],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            print(f"[{name}] {'PASS' if completed.returncode == 0 else 'FAIL'}", flush=True)
            print(completed.stdout.strip(), flush=True)
            if completed.stderr:
                print(completed.stderr.strip(), flush=True)
            checks.append({"name": name, "passed": completed.returncode == 0})
        cases = (
            [
                {
                    "name": case.attrib["name"],
                    "passed": not any(
                        case.find(tag) is not None for tag in ("failure", "error", "skipped")
                    ),
                }
                for case in ET.parse(xml).iter("testcase")
            ]
            if xml.exists()
            else []
        )
    passed = all(check["passed"] for check in checks)
    if args.report:
        artifacts = []
        for directory in [
            *TARGETS,
            "src/aether_agent_memory/runtime/foundation",
            "tests/runtime/flows",
            "scripts/p3",
        ]:
            for path in (ROOT / directory).rglob("*.py"):
                artifacts.append(
                    {
                        "path": path.relative_to(ROOT).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "profile": "basic_local",
            "passed": passed,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "checks": checks,
            "flow_cases": cases,
            "not_run": [
                "live_langmem_model",
                "semantic_embedding_model",
                "milvus",
                "production_storage_tiers",
                "http_host",
                "azure_aks",
                "metrics_and_soak",
            ],
            "artifacts": artifacts,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
