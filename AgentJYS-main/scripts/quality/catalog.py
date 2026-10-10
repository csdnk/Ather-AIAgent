"""Validate the shared specification catalog and select conservative gates.

Selection is a requirement list, never proof that its automation is implemented.
Execution results live in separate immutable run reports.
"""

import argparse
import hashlib
import json
from pathlib import Path

COUNTS = {
    "ENV": 6,
    "CT": 4,
    "SEC": 9,
    "REM": 15,
    "REC": 11,
    "OPS": 6,
    "APP": 9,
    "REL": 6,
    "PERF": 11,
    "DEP": 6,
    "E2E": 5,
}
BASELINE_IDS = {f"{prefix}-{n:02}" for prefix, count in COUNTS.items() for n in range(1, count + 1)}
CATALOG_DIR = Path(__file__).parents[2] / "tests/catalog"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def validate_catalog(catalog, root):
    issues = []
    rows = catalog.get("cases", [])
    if not isinstance(rows, list):
        return ["cases must be an array"]
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or len(ids) != len(set(ids)):
        issues.append("invalid or duplicate case IDs")
    if set(ids) != BASELINE_IDS:
        issues.append("baseline IDs missing or unexpected")
    root = Path(root).resolve()
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = row.get("id", "?")
        for key in ("title", "preconditions", "cleanup", "evidence"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                issues.append(f"{label}: missing {key}")
        for key in ("steps", "expected", "requirements"):
            if (
                not isinstance(row.get(key), list)
                or not row[key]
                or any(not isinstance(v, str) or not v.strip() for v in row[key])
            ):
                issues.append(f"{label}: empty or invalid {key}")
        if any(k in row for k in ("status", "run_id", "actual_result", "passed")):
            issues.append(f"{label}: execution state mixed into specification")
        entries = row.get("automation", [])
        if not isinstance(entries, list):
            issues.append(f"{label}: invalid automation list")
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                issues.append(f"{label}: invalid entrypoint")
                continue
            file = (root / entry.get("path", "")).resolve()
            if not file.is_relative_to(root) or not file.is_file():
                issues.append(f"{label}: missing or external entrypoint")
            if entry.get("coverage") not in ("partial", "full"):
                issues.append(f"{label}: unknown coverage")
            if entry.get("dependency") not in ("real", "mock", "replay", "static"):
                issues.append(f"{label}: undeclared dependency kind")
            if entry.get("coverage") == "full" and row.get("open_design_items"):
                issues.append(f"{label}: unresolved design cannot claim full coverage")
    return issues


def select_cases(catalog, changes, profile):
    profiles = json.loads((CATALOG_DIR / "profiles.json").read_text(encoding="utf-8"))
    rules = json.loads((CATALOG_DIR / "impact-rules.json").read_text(encoding="utf-8"))
    if profile not in profiles["profiles"]:
        raise ValueError("unknown test profile")
    paths = [p.replace("\\", "/").removeprefix("./") for p in changes]
    rows = catalog["cases"]
    all_ids = [row["id"] for row in rows]
    # Until empirical impact mapping exists, select all product requirements.
    # Broad selection exposes unimplemented tests instead of silently omitting them.
    selected, reason = all_ids, "product_change_conservative_full_selection"
    if profile == "release":
        reason = "release_full_selection"
    elif profile == "post-deploy":
        reason = "deployed_candidate_full_selection"
    elif paths and all(
        p.startswith(rules["documentation_only"]["prefix"])
        and p.endswith(rules["documentation_only"]["suffix"])
        and ".." not in p.split("/")
        for p in paths
    ):
        selected, reason = [], "documentation_only_no_product_gate"
    elif not paths or any(not p.startswith(tuple(rules["product_roots"])) for p in paths):
        reason = "unknown_path_full_selection"
    elif any(
        p.endswith(tuple(rules["dependency_suffixes"]))
        or any(fragment in p for fragment in rules["deployment_fragments"])
        for p in paths
    ):
        reason = "dependency_or_deployment_full_selection"
    return {
        "profile": profile,
        "case_ids": selected,
        "reason": reason,
        "policy_sha256": digest({"profiles": profiles, "impact_rules": rules}),
        "catalog_version": catalog["catalog_version"],
        "catalog_sha256": digest(catalog),
        "unimplemented_case_ids": [
            r["id"]
            for r in rows
            if r["id"] in selected and not any(a["coverage"] == "full" for a in r["automation"])
        ],
    }


def render_catalog(catalog):
    lines = [
        "# Aether 统一测试用例台账",
        "",
        "此文件由代码仓库 tests/catalog/cases.json 生成；修改源目录后重新生成。规格设计、自动化覆盖与执行结果分别维护。",  # noqa: E501
        "",
        f"目录版本：{catalog['catalog_version']}。规格数：{len(catalog['cases'])}。目录摘要：`{digest(catalog)}`。",
        "",
        "目前所有规格仍有未展开或未验证范围；部分场景运行通过不能将整项规格标为通过。",
        "",
    ]
    for row in catalog["cases"]:
        lines.extend(
            [
                f"## {row['id']} {row['title']}",
                "",
                f"需求：{', '.join(row['requirements'])}；层级：{row['level']}；设计状态：{row['design_state']}。",  # noqa: E501
                "",
                f"前置条件：{row['preconditions']}",
                "",
                f"数据集：{row['dataset']}",
                "",
                "步骤：",
                "",
            ]
        )
        lines.extend(f"{i}. {step}" for i, step in enumerate(row["steps"], 1))
        lines.extend(["", "预期：", ""] + ["- " + x for x in row["expected"]])
        lines.extend(
            ["", f"清理：{row['cleanup']}", "", f"证据：{row['evidence']}", "", "自动化映射：", ""]
        )
        lines.extend(
            [
                f"- {a['path']}；场景 {', '.join(a.get('scenario_keys', []))}；{a['coverage']}；依赖 {a['dependency']}"  # noqa: E501
                for a in row["automation"]
            ]
            or ["- 未实现可执行入口。"]
        )
        lines.extend(["", "未完成范围：", ""] + ["- " + x for x in row["open_design_items"]] + [""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", type=Path, default=Path(__file__).parents[2] / "tests/catalog/cases.json"
    )
    parser.add_argument("--profile", choices=["commit", "post-deploy", "release"], default="commit")
    parser.add_argument("--changed", nargs="*", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path, help="生成用户用例台账，禁止覆盖已有文件")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    issues = validate_catalog(catalog, Path(__file__).parents[2])
    result = {
        "validation": "FAIL" if issues else "PASS",
        "issues": issues,
        "selection": None if issues else select_cases(catalog, args.changed, args.profile),
    }
    if args.markdown and not issues:
        with args.markdown.open("x", encoding="utf-8") as stream:
            stream.write(render_catalog(catalog))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text)
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
