"""Run the B2 compression gate across every JSONL corpus in a dataset root.

The P3 acceptance flow uses this entry point instead of hard-coding one or two
corpora.  Inputs are discovered recursively and processed in a stable path
order.  Each corpus gets a detailed report, while ``--output`` receives a
single aggregate report with sample counts, byte totals, weighted compression
ratio and the compression gate status.

The script intentionally consumes normalized JSONL files.  Raw source files
(for example, BEAM Parquet shards) must first be normalized into the dataset
root so that the exact input scope is recorded and reproducible.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

# Keep direct execution from a mounted checkout independent of the image's
# editable-install timestamp.  The acceptance runner invokes this file by
# path, so the repository root and current ``src`` tree need explicit paths.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _source_root in (_REPO_ROOT, _REPO_ROOT / "src"):
    _source_text = str(_source_root)
    if _source_root.is_dir() and _source_text not in sys.path:
        sys.path.insert(0, _source_text)

try:  # Direct execution uses the benchmark directory as sys.path[0].
    from benchmarks.p3.b2_compression import run as run_corpus
except ModuleNotFoundError:  # pragma: no cover - exercised by direct CLI use
    from b2_compression import run as run_corpus


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def discover_jsonl(root: Path) -> list[Path]:
    """Return non-empty JSONL corpora below ``root`` in deterministic order."""

    if not root.is_dir():
        raise SystemExit(f"dataset root does not exist or is not a directory: {root}")
    inputs = sorted(
        (path for path in root.rglob("*.jsonl") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if not inputs:
        raise SystemExit(f"no JSONL corpora found below dataset root: {root}")
    return inputs


def _detail_filename(root: Path, input_path: Path) -> Path:
    relative = input_path.relative_to(root)
    # Keep the source directory visible in the report name and avoid writing
    # outside the output directory when a caller supplies nested inputs.
    return Path(*relative.with_suffix(".json").parts)


def _aggregate(
    root: Path,
    target_ratio: float,
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    ratios: list[float] = []
    original_bytes = 0
    compressed_bytes = 0
    sample_count = 0
    usable_count = 0
    passed_count = 0
    below_target_count = 0
    skipped_count = 0
    invalid_count = 0
    for detail in details:
        sample_count += int(detail["sample_count"])
        usable_count += int(detail["usable_count"])
        passed_count += int(detail["passed_count"])
        below_target_count += int(detail["below_target_count"])
        skipped_count += int(detail["skipped_count"])
        invalid_count += int(detail["invalid_count"])
        for sample in detail["samples"]:
            ratio = sample.get("compression_ratio")
            if ratio is not None:
                ratios.append(float(ratio))
            original_bytes += int(sample.get("original_utf8_bytes") or 0)
            compressed_bytes += int(sample.get("compressed_utf8_bytes") or 0)

    gate_status = (
        "PASS"
        if sample_count > 0
        and usable_count == sample_count
        and passed_count == sample_count
        else "NOT_READY"
    )
    return {
        "target_ratio": target_ratio,
        "dataset_root": root.as_posix(),
        "source_file_count": len(details),
        "sample_count": sample_count,
        "usable_count": usable_count,
        "passed_count": passed_count,
        "below_target_count": below_target_count,
        "skipped_count": skipped_count,
        "invalid_count": invalid_count,
        "original_utf8_bytes": original_bytes,
        "compressed_utf8_bytes": compressed_bytes,
        "weighted_compression_ratio": (
            original_bytes / compressed_bytes if compressed_bytes else None
        ),
        "mean_ratio": statistics.mean(ratios) if ratios else None,
        "p50_ratio": _percentile(ratios, 0.50),
        "p95_ratio": _percentile(ratios, 0.95),
        "p99_ratio": _percentile(ratios, 0.99),
        "min_ratio": min(ratios) if ratios else None,
        "max_ratio": max(ratios) if ratios else None,
        "compression_gate_status": gate_status,
        # Compression alone is not a semantic quality evaluation.
        "acceptance_status": "NOT_READY_QUALITY_EVALUATION",
        "datasets": details,
    }


def run_dataset(root: Path, output_path: Path, target_ratio: float) -> dict[str, Any]:
    """Compress every JSONL corpus below ``root`` and write detailed reports."""

    inputs = discover_jsonl(root)
    detail_root = output_path.parent / f"{output_path.stem}_datasets"
    details: list[dict[str, Any]] = []
    for input_path in inputs:
        relative = input_path.relative_to(root)
        detail_path = detail_root / _detail_filename(root, input_path)
        detail = run_corpus(
            input_path,
            detail_path,
            target_ratio,
            source_prefix=f"dataset:{relative.as_posix()}",
        )
        details.append(
            {
                "dataset": relative.as_posix(),
                "input_bytes": input_path.stat().st_size,
                "report": detail_path.relative_to(output_path.parent).as_posix(),
                **detail,
            }
        )

    summary = _aggregate(root, target_ratio, details)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-root",
        type=Path,
        required=True,
        help="dataset directory; every nested *.jsonl file is included",
    )
    parser.add_argument("--output", type=Path, required=True, help="aggregate JSON report path")
    parser.add_argument("--target-ratio", type=float, default=5.0)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when every discovered sample does not meet the gate",
    )
    args = parser.parse_args()
    if args.target_ratio <= 1.0:
        raise SystemExit("--target-ratio must be greater than 1")
    summary = run_dataset(args.input_root, args.output, args.target_ratio)
    compact = {key: value for key, value in summary.items() if key != "datasets"}
    print(json.dumps(compact, ensure_ascii=False))
    if args.strict and summary["compression_gate_status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
