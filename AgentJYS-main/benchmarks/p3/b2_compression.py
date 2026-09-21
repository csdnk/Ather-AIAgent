"""Measure B2 persisted compression Artifacts on a JSONL corpus.

Each input line may be a string or an object containing ``text``, ``content``,
``context``, or ``input``.  The script never replaces a missing/short sample
with synthetic data; it reports ``NOT_READY`` when the corpus cannot support
the target gate.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

# The server acceptance runner executes this file by path from
# ``/workspace/benchmarks/p3``.  In that mode Python does not automatically
# put the repository root or ``src`` tree on ``sys.path``.  Prefer the mounted
# source tree so a rebuilt image is not required just to pick up B2 changes.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _source_root in (_REPO_ROOT, _REPO_ROOT / "src"):
    _source_text = str(_source_root)
    if _source_root.is_dir() and _source_text not in sys.path:
        sys.path.insert(0, _source_text)

from aether_agent_memory.b2.compression import HybridMemoryCompressor  # noqa: E402


def _text_from_record(record: Any) -> str:
    if isinstance(record, str):
        return record
    if isinstance(record, dict):
        for key in ("text", "content", "context", "input"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value
        # Common dataset envelopes keep the dialogue under a list of turns.
        for key in ("conversation", "dialogue", "messages", "turns"):
            value = record.get(key)
            if isinstance(value, list):
                parts = _sequence_text(value)
                joined = "\n".join(part for part in parts if part.strip())
                if joined:
                    return joined
        for key in ("sessions", "haystack_sessions"):
            value = record.get(key)
            if isinstance(value, list):
                parts = _sequence_text(value)
                joined = "\n".join(part for part in parts if part.strip())
                if joined:
                    return joined
    return ""


def _sequence_text(items: list[Any]) -> list[str]:
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("text") or item.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content)
        for nested_key in ("turns", "messages", "conversation"):
            nested = item.get(nested_key)
            if isinstance(nested, list):
                parts.extend(_sequence_text(nested))
    return parts


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    values = sorted(values)
    rank = (len(values) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    fraction = rank - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def run(
    input_path: Path,
    output_path: Path,
    target_ratio: float,
    *,
    source_prefix: str = "benchmark",
) -> dict[str, Any]:
    compressor = HybridMemoryCompressor()
    samples: list[dict[str, Any]] = []
    ratios: list[float] = []
    with input_path.open("r", encoding="utf-8") as source:
        for index, line in enumerate(source, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            text = _text_from_record(record)
            if not text:
                samples.append({"line": index, "status": "invalid", "warning": "no text field"})
                continue
            artifact = compressor.compress(text, source_memory_id=f"{source_prefix}:{index}")
            ratios.append(artifact.compression_ratio)
            samples.append(
                {
                    "line": index,
                    "artifact_id": artifact.artifact_id,
                    "status": artifact.status,
                    "quality_status": artifact.quality_status,
                    "compression_ratio": artifact.compression_ratio,
                    "original_utf8_bytes": artifact.original_utf8_bytes,
                    "compressed_utf8_bytes": artifact.compressed_utf8_bytes,
                    "original_token_count": artifact.original_token_count,
                    "compressed_token_count": artifact.compressed_token_count,
                    "warnings": artifact.warnings,
                }
            )

    usable = [sample for sample in samples if sample.get("compression_ratio") is not None]
    passed = [
        sample
        for sample in usable
        if sample["status"] == "compressed"
        and sample["quality_status"] == "protected_markers_retained"
        and float(sample["compression_ratio"]) >= target_ratio
    ]
    summary = {
        "target_ratio": target_ratio,
        "sample_count": len(samples),
        "usable_count": len(usable),
        "passed_count": len(passed),
        "below_target_count": sum(sample.get("status") == "below_target" for sample in samples),
        "skipped_count": sum(sample.get("status") == "skipped" for sample in samples),
        "invalid_count": sum(sample.get("status") == "invalid" for sample in samples),
        "mean_ratio": statistics.mean(ratios) if ratios else None,
        "p50_ratio": _percentile(ratios, 0.50),
        "p95_ratio": _percentile(ratios, 0.95),
        "p99_ratio": _percentile(ratios, 0.99),
        "min_ratio": min(ratios) if ratios else None,
        "compression_gate_status": (
            "PASS"
            if samples
            and len(passed) == len(samples)
            and len(usable) == len(samples)
            else "NOT_READY"
        ),
        "acceptance_status": "NOT_READY_QUALITY_EVALUATION",
        "samples": samples,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 JSONL corpus")
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    parser.add_argument("--target-ratio", type=float, default=5.0)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when every sample does not meet the compression gate",
    )
    args = parser.parse_args()
    if args.target_ratio <= 1.0:
        raise SystemExit("--target-ratio must be greater than 1")
    summary = run(args.input, args.output, args.target_ratio)
    compact = {key: value for key, value in summary.items() if key != "samples"}
    print(json.dumps(compact, ensure_ascii=False))
    if args.strict and summary["compression_gate_status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
