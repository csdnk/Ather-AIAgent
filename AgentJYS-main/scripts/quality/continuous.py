"""One report contract for commit checks and deployment regression.

Reports never equate a passing subset with full release acceptance. Raw subprocess
logs stay in the caller's output directory; only safe allowlisted fields are shared.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_RUN", "N/A")


def verdict(rows):
    states = [r["status"] for r in rows if r.get("required", True)]
    if any(r["status"] == "FAIL" for r in rows):
        return "FAIL"
    if "FAIL" in states:
        return "FAIL"
    if not states or any(s in ("BLOCKED", "NOT_RUN") for s in states):
        return "BLOCKED"
    return "PASS" if "PASS" in states else "BLOCKED"


def junit_rows(file, layer):
    try:
        cases = list(ET.parse(file).getroot().iter("testcase"))
        if not cases:
            raise ValueError("empty test evidence")
    except (OSError, ET.ParseError, ValueError):
        return [
            dict(
                id=layer + "/evidence",
                layer=layer,
                status="BLOCKED",
                reason="missing, empty or invalid JUnit evidence",
            )
        ]
    rows = []
    for case in cases:
        status = (
            "FAIL"
            if case.find("failure") is not None
            else "BLOCKED"
            if case.find("error") is not None
            else "NOT_RUN"
            if case.find("skipped") is not None
            else "PASS"
        )
        rows.append(
            dict(
                id=case.get("classname", "") + "/" + case.get("name", ""),
                layer=layer,
                status=status,
                seconds=case.get("time"),
                reason="JUnit " + status,
            )
        )
    return rows


def live_rows(journal, result):
    cleanup = journal.get("cleanup", {})
    accounts = journal.get("accounts", [])
    account_ok = bool(accounts) and all(
        a.get("cleanup") in ("deleted_and_absence_verified", "not_created_verified")
        for a in accounts
    )
    return [
        dict(
            id="live/business",
            layer="system",
            status=journal.get("result", "BLOCKED"),
            reason="native HTTP steps through compatibility executor",
            run_id=journal.get("run_id"),
            requests=result.get("requests", 0),
        ),
        dict(
            id="live/data-cleanup",
            layer="cleanup",
            status=cleanup.get("status", "BLOCKED"),
            reason=cleanup.get("restoration_scope", "inspect cleanup journal"),
            physical_restored=cleanup.get("physical_restored", False),
            source_retention=cleanup.get("source_retention"),
        ),
        dict(
            id="live/account-cleanup",
            layer="cleanup",
            status="PASS" if account_ok else "BLOCKED",
            reason="exact owned account absence verification",
        ),
    ]


def candidate():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

    return {
        "sha": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "test_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def emit(output, rows, source, profile):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(r.get("status") not in STATUSES for r in rows):
        raise ValueError("invalid execution status")
    counts = dict.fromkeys(STATUSES, 0)
    counts.update(Counter(r["status"] for r in rows))
    report = dict(
        schema_version=1,
        run_id=str(uuid.uuid4()),
        created_at=datetime.now(UTC).isoformat(),
        profile=profile,
        candidate=source,
        execution_status=verdict(rows),
        release_gate="BLOCKED",
        counts=counts,
        records=rows,
        scope="execution instances; not specification coverage or full release acceptance",
    )
    with (output / "summary.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    lines = [
        "# Aether 测试结果",
        "",
        f"本轮执行：{report['execution_status']}；上线门禁：BLOCKED。",
        f"运行编号：{report['run_id']}；配置：{profile}。",
        f"候选源码：{source.get('sha', 'unknown')}；含未提交改动：{source.get('dirty', 'unknown')}。",  # noqa: E501
        "",
        "这些是执行实例数量，不能当作完整用例规格覆盖率。",
        "",
        " | ".join(f"{k}: {v}" for k, v in counts.items()),
        "",
        "| 层级 | 状态 | 用例/检查 | 说明 |",
        "|---|---|---|---|",
    ]
    for row in rows:

        def clean(s):
            return str(s).replace("|", "/").replace("\n", " ")

        lines.append(
            "| "
            + " | ".join(clean(row.get(k, "")) for k in ["layer", "status", "id", "reason"])
            + " |"
        )
    lines += [
        "",
        "Codex 汇报要求：说明实际失败、阻塞、未执行和清理状态；不得把子集通过描述为全项目通过。",
        "数据清理 PASS 仅覆盖业务不可见及提供方清理，physical_restored=false 时源文件/审计仍按产品策略保留。",  # noqa: E501
        "每次复测创建新的运行目录，保留首次失败。正式性能、容量、浏览器、恢复及稳定性须单独提供证据。",
    ]
    (output / "agent-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    suite = ET.Element(
        "testsuite",
        name=profile,
        tests=str(len(rows)),
        failures=str(counts["FAIL"]),
        errors=str(counts["BLOCKED"]),
        skipped=str(counts["NOT_RUN"] + counts["N/A"]),
    )
    for row in rows:
        case = ET.SubElement(suite, "testcase", classname=row["layer"], name=row["id"])
        tag = {"FAIL": "failure", "BLOCKED": "error", "NOT_RUN": "skipped", "N/A": "skipped"}.get(
            row["status"]
        )
        if tag:
            ET.SubElement(case, tag, message=row.get("reason", row["status"]))
    ET.ElementTree(suite).write(output / "results.xml", encoding="utf-8", xml_declaration=True)
    return report


def run_command(command, output, timeout):
    with Path(output).open("wb") as stream:
        try:
            return subprocess.run(
                command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout
            ).returncode
        except (OSError, subprocess.TimeoutExpired):
            return 2


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", choices=["commit", "post-deploy", "collect"], required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--junit", action="append", default=[], help="layer=absolute_file")
    p.add_argument("--live-result", type=Path, help="existing immutable live run directory")
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    rows = []
    if a.profile == "commit":
        file = a.output / "quality-python.xml"
        rc = run_command(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/quality",
                "--confcutdir=tests/quality",
                "-q",
                "--junitxml=" + str(file),
            ],
            a.output / "python.log",
            300,
        )
        rows.extend(junit_rows(file, "test-tooling"))
        if rc and verdict(rows) == "PASS":
            rows.append(
                dict(
                    id="python/exit",
                    layer="test-tooling",
                    status="BLOCKED",
                    reason="runner exit disagrees with assertions",
                )
            )
        files = sorted(str(f) for f in (ROOT / "tests/quality").glob("*.test.cjs"))
        rc = run_command(["node", "--test", *files], a.output / "node.log", 300)
        rows.append(
            dict(
                id="node/quality",
                layer="test-tooling",
                status="PASS" if rc == 0 and files else "FAIL",
                reason="Node quality automation regression",
            )
        )
        # Commit mode proves the testing tools, while product results are supplied
        # by the independent unit/integration jobs via --junit.
    for entry in a.junit:
        layer, file = entry.split("=", 1)
        rows.extend(junit_rows(Path(file), layer))
    if a.live_result:
        try:
            journal = a.live_result / (
                "reconciliation.json"
                if (a.live_result / "reconciliation.json").exists()
                else "journal.json"
            )
            rows.extend(
                live_rows(
                    json.loads(journal.read_text(encoding="utf-8")),
                    json.loads((a.live_result / "result.json").read_text(encoding="utf-8")),
                )
            )
        except (OSError, ValueError):
            rows.append(
                dict(
                    id="live/evidence",
                    layer="system",
                    status="BLOCKED",
                    reason="missing or invalid live evidence",
                )
            )
    if a.profile == "post-deploy":
        for layer in [
            "browser-e2e",
            "performance",
            "capacity",
            "security-full",
            "recovery",
            "soak",
        ]:
            rows.append(
                dict(
                    id=layer + "/acceptance",
                    layer=layer,
                    status="NOT_RUN",
                    required=False,
                    reason="separate release evidence; outside this deployment functional gate",
                )
            )
    report = emit(a.output, rows, candidate(), a.profile)
    print(
        json.dumps({k: report[k] for k in ["run_id", "execution_status", "counts", "release_gate"]})
    )
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["execution_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
