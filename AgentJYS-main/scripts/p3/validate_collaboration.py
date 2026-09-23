"""Shared local/GitHub/Azure gate. Does not claim live infrastructure acceptance."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check_entrypoints() -> None:
    """The standalone checkout must contain its contribution links and gate files."""
    required = [
        "CONTRIBUTING.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/workflows/p3-collaboration.yml",
        "azure-pipelines.p3.yml",
        "scripts/p3/requirements-collaboration.txt",
    ]
    for name in required:
        if not (ROOT / name).is_file():
            raise ValueError(f"missing collaboration file: {name}")
    content = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)]+)\)", content):
        if not target.startswith(("https://", "http://", "#")) and not (
            ROOT / target.split("#")[0]
        ).exists():
            raise ValueError(f"broken contribution link: {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Raw JSON evidence; prefer outside checkout")
    args = parser.parse_args()
    checks: list[dict[str, object]] = []
    try:
        check_entrypoints()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    checks.append({"name": "collaboration_entrypoints", "passed": True})
    with tempfile.TemporaryDirectory(prefix="p3-collaboration-") as temporary:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
        # 门禁覆盖新增 A 流程、原生模型空间模块及 HTTP 验收脚本的静态检查。
        # 门禁本身不加载真实模型；真实模型和 TCP HTTP 验收需要另行运行专用脚本。
        commands = [
            ("flows_foundation_contracts", ["scripts/p3/validate_flows.py"]),
            (
                "legacy_recall_and_native_adapters",
                [
                    "-m",
                    "pytest",
                    "tests/unit/recall",
                    "tests/runtime/native",
                    "-q",
                    "-o",
                    f"cache_dir={temporary}/pytest",
                ],
            ),
            (
                "native_lint",
                [
                    "-m",
                    "ruff",
                    "check",
                    "--no-cache",
                    "src/aether_agent_memory/recall/embedding/p3.py",
                    "src/aether_agent_memory/recall/embedding/spaces.py",
                    "tests/runtime/native",
                    "scripts/p3/validate_native_flows.py",
                    "scripts/p3/validate_native_http.py",
                ],
            ),
            (
                "native_types",
                [
                    "-m",
                    "mypy",
                    "--config-file",
                    "scripts/p3/mypy.ini",
                    "--cache-dir",
                    f"{temporary}/mypy",
                    "src/aether_agent_memory/recall/embedding/p3.py",
                    "src/aether_agent_memory/recall/embedding/spaces.py",
                ],
            ),
        ]
        for name, command in commands:
            print(f"[{name}] running", flush=True)
            result = subprocess.run([sys.executable, *command], cwd=ROOT, env=env, check=False)
            checks.append({"name": name, "passed": result.returncode == 0})
            print(f"[{name}] {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
    passed = all(check["passed"] for check in checks)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "p3_collaboration_gate",
        "passed": passed,
        "checks": checks,
        "not_verified": [
            "live_langmem",
            "live_embedding_and_reranker",
            "live_milvus",
            "azure_or_aks",
            "remote_ci_execution",
            "branch_protection",
            "product_acceptance",
        ],
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
