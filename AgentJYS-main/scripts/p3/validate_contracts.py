"""One command for contract-only checks; no application services are started."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from catalog import FLOWS, ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Optional final contract-only JSON summary")
    args = parser.parse_args()
    targets = [f"src/aether_agent_memory/{flow}/contracts" for flow in FLOWS]
    checks = []
    with tempfile.TemporaryDirectory(prefix="p3-contract-check-") as tmp:
        commands = [
            ("schema_drift", ["scripts/p3/generate_schemas.py", "--check"]),
            (
                "contract_tests",
                [
                    "-m",
                    "pytest",
                    "-c",
                    "tests/contracts/p3/pytest.ini",
                    "tests/contracts/p3",
                    "-q",
                    "-o",
                    f"cache_dir={tmp}/pytest",
                ],
            ),
            (
                "lint",
                ["-m", "ruff", "check", "--no-cache", *targets, "scripts/p3", "tests/contracts/p3"],
            ),
            (
                "type_check",
                [
                    "-m",
                    "mypy",
                    "--config-file",
                    "scripts/p3/mypy.ini",
                    "--cache-dir",
                    f"{tmp}/mypy",
                    *targets,
                ],
            ),
        ]
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
                {"check": name, "passed": result.returncode == 0, "exit_code": result.returncode}
            )
    passed = all(check["passed"] for check in checks)
    if args.report:
        paths = []
        for directory in [*targets, "scripts/p3", "tests/contracts/p3", "contracts/p3", "docs/p3"]:
            for path in (ROOT / directory).rglob("*"):
                if (
                    path.is_file()
                    and "__pycache__" not in path.parts
                    and "reports" not in path.parts
                    and path.suffix in {".py", ".md", ".json", ".yaml", ".ini", ".txt"}
                ):
                    paths.append(path)
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": "contract_only_not_runtime_or_product_acceptance",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "passed": passed,
            "checks": checks,
            "not_run": [
                "database_atomicity",
                "worker_recovery",
                "real_model",
                "milvus",
                "real_actuation",
                "azure",
                "aks",
                "mvp_24h",
                "final_72h",
            ],
            "artifacts": [
                {
                    "path": p.relative_to(ROOT).as_posix(),
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                }
                for p in sorted(set(paths))
            ],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
