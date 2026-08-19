"""Write auditable two-phase metrics for the B1/B2 acceptance workflow."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "MISSING", "path": str(path)}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return {"status": "INVALID", "path": str(path)}
    return value


def phase_one(result: dict[str, Any]) -> dict[str, Any]:
    best = result.get("best_primary") or {}
    text = result.get("text") or {}
    return {
        "name": "phase1_b1_real_dataset_embedding",
        "status": result.get("status", "MISSING"),
        "method": result.get("method"),
        "dataset": {
            "root": text.get("dataset_root"),
            "sample_count": text.get("dataset_sample_count"),
            "utf8_bytes": text.get("utf8_bytes"),
        },
        "sidecar": {
            "backend": (result.get("health") or {}).get("backend"),
            "dimension": (result.get("health") or {}).get("dimension"),
            "model": (result.get("health") or {}).get("model_name"),
        },
        "throughput": {
            "request_qps": best.get("request_qps"),
            "item_per_second": best.get("item_per_second"),
            "error_rate": best.get("error_rate"),
            "p50_ms": best.get("p50_ms"),
            "p95_ms": best.get("p95_ms"),
            "p99_ms": best.get("p99_ms"),
            "profiling": best.get("profiling"),
        },
        "contract": result.get("contract"),
    }


def phase_two(
    compression: dict[str, Any], replay: dict[str, Any], smoke_log: Path
) -> dict[str, Any]:
    smoke_passed = smoke_log.is_file() and "COMPOSE_SMOKE_PASSED" in smoke_log.read_text(
        encoding="utf-8", errors="replace"
    )
    return {
        "name": "phase2_b2_memory_compression_and_retrieval",
        "status": "PASSED" if smoke_passed and replay.get("samples", 0) > 0 else "FAILED",
        "async_b1_to_p2_to_b2_to_b3_smoke_passed": smoke_passed,
        "compression": {
            "dataset_root": compression.get("dataset_root"),
            "sample_count": compression.get("sample_count"),
            "weighted_compression_ratio": compression.get("weighted_compression_ratio"),
            "p50_ratio": compression.get("p50_ratio"),
            "p95_ratio": compression.get("p95_ratio"),
            "p99_ratio": compression.get("p99_ratio"),
            "gate_status": compression.get("compression_gate_status"),
            "quality_status": compression.get("acceptance_status"),
        },
        "replay": {
            key: replay.get(key)
            for key in (
                "samples",
                "working_writes",
                "episodic_writes",
                "semantic_writes",
                "queries",
                "working_write_ms_p50_ms",
                "working_write_ms_p99_ms",
                "episodic_write_ms_p50_ms",
                "episodic_write_ms_p99_ms",
                "semantic_write_ms_p50_ms",
                "semantic_write_ms_p99_ms",
                "context_recall_ms_p50_ms",
                "context_recall_ms_p99_ms",
            )
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--b1-result", type=Path, required=True)
    parser.add_argument("--b2-compression", type=Path, required=True)
    parser.add_argument("--b2-replay", type=Path, required=True)
    parser.add_argument("--smoke-log", type=Path, required=True)
    args = parser.parse_args()

    phase1 = phase_one(load_json(args.b1_result))
    phase2 = phase_two(load_json(args.b2_compression), load_json(args.b2_replay), args.smoke_log)
    overall = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "PASSED"
        if phase1["status"] == "COMPLETED" and phase2["status"] == "PASSED"
        else "FAILED",
        "quality_evaluation": phase2["compression"]["quality_status"],
        "phase_reports": ["phase1_b1_metrics.json", "phase2_b2_metrics.json"],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("phase1_b1_metrics.json", phase1),
        ("phase2_b2_metrics.json", phase2),
        ("full_b1_b2_summary.json", overall),
    ):
        (args.output_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(overall, ensure_ascii=False))


if __name__ == "__main__":
    main()
