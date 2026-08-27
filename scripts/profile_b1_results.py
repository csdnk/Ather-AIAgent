from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path
from typing import Any

STAGES = (
    "network",
    "validation",
    "chunking",
    "queue_wait",
    "model_inference",
    "vector_postprocess",
    "response_postprocess",
    "unaccounted",
)
PERCENTILES = ("p50", "p95", "p99")


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def load_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("batch_*/results.json")):
        batch_size = int(path.parent.name.split("_")[-1])
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("results", [])
        record: dict[str, Any] = {
            "batch_size": batch_size,
            "concurrency": rows[0].get("concurrency") if rows else None,
            "repeat_count": len(rows),
        }
        for stage in STAGES + ("server_total",):
            for percentile in PERCENTILES:
                key = f"profiling_{stage}_{percentile}_ms"
                values = [
                    float(row[key])
                    for row in rows
                    if row.get(key) is not None and math.isfinite(float(row[key]))
                ]
                record[key] = _mean(values)
        records.append(record)
    return sorted(records, key=lambda row: int(row["batch_size"]))


def write_outputs(root: Path, records: list[dict[str, Any]]) -> None:
    fields = ["batch_size", "concurrency", "repeat_count"]
    for stage in STAGES + ("server_total",):
        fields.extend(f"profiling_{stage}_{percentile}_ms" for percentile in PERCENTILES)
    with (root / "profiling_summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    (root / "profiling_summary.json").write_text(
        json.dumps(
            {"stages": STAGES, "percentiles": PERCENTILES, "results": records},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    write_svg(root / "profiling_breakdown.svg", records)


def write_svg(path: Path, records: list[dict[str, Any]]) -> None:
    width, height = 1500, 900
    left, right = 90, 35
    bar_top, bar_height = 80, 330
    line_top, line_height = 500, 300
    plot_width = width - left - right
    colors = [
        "#64748b",
        "#2563eb",
        "#f59e0b",
        "#8b5cf6",
        "#dc2626",
        "#059669",
        "#0891b2",
        "#94a3b8",
    ]
    batch_sizes = [int(record["batch_size"]) for record in records]

    def text(x: float, y: float, value: object, size: int = 12, anchor: str = "start") -> str:
        return (
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial,sans-serif" '
            f'font-size="{size}px" text-anchor="{anchor}" fill="#1f2937">'
            f"{html.escape(str(value))}</text>"
        )

    def x_for(index: int) -> float:
        if len(batch_sizes) <= 1:
            return left + plot_width / 2
        return left + index * plot_width / (len(batch_sizes) - 1)

    lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
        ),
        '<rect width="100%" height="100%" fill="white"/>',
        text(width / 2, 32, "B1 profiling by batch size", 22, "middle"),
        text(
            width / 2,
            56,
            "P50 stage breakdown and total P50/P95/P99; "
            "network = client total - server total",
            13,
            "middle",
        ),
    ]
    stack_values = [
        sum(float(record.get(f"profiling_{stage}_p50_ms") or 0.0) for stage in STAGES)
        for record in records
    ]
    stack_max = max(stack_values, default=1.0) * 1.15
    stack_max = max(stack_max, 1.0)
    lines.append(text(left, bar_top - 12, "P50 stage breakdown (ms)", 16))
    for tick in range(5):
        value = stack_max * tick / 4
        y = bar_top + bar_height - value / stack_max * bar_height
        lines.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" '
            f'y2="{y:.1f}" stroke="#e2e8f0"/>'
        )
        lines.append(text(left - 8, y + 4, f"{value:.0f}", 11, "end"))
    bar_width = max(12.0, min(52.0, plot_width / max(len(records) * 1.6, 1)))
    for index, record in enumerate(records):
        x = x_for(index) - bar_width / 2
        accumulated = 0.0
        for color, stage in zip(colors, STAGES, strict=True):
            value = float(record.get(f"profiling_{stage}_p50_ms") or 0.0)
            if value <= 0:
                continue
            segment_height = value / stack_max * bar_height
            y = bar_top + bar_height - (accumulated + value) / stack_max * bar_height
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
                f'height="{segment_height:.1f}" fill="{color}"/>'
            )
            accumulated += value
        lines.append(
            text(
                x + bar_width / 2,
                bar_top + bar_height + 19,
                record["batch_size"],
                11,
                "middle",
            )
        )
    for index, stage in enumerate(STAGES):
        x = left + (index % 4) * 210
        y = bar_top + bar_height + 42 + (index // 4) * 18
        lines.append(f'<rect x="{x}" y="{y - 11}" width="12" height="12" fill="{colors[index]}"/>')
        lines.append(text(x + 18, y, stage, 11))

    lines.append(text(left, line_top - 12, "Total server latency percentiles (ms)", 16))
    total_values = [
        float(record.get(f"profiling_server_total_{percentile}_ms") or 0.0)
        for record in records
        for percentile in PERCENTILES
    ]
    line_max = max(total_values, default=1.0) * 1.15
    line_max = max(line_max, 1.0)
    for tick in range(5):
        value = line_max * tick / 4
        y = line_top + line_height - value / line_max * line_height
        lines.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" '
            f'y2="{y:.1f}" stroke="#e2e8f0"/>'
        )
        lines.append(text(left - 8, y + 4, f"{value:.0f}", 11, "end"))
    line_colors = {"p50": "#2563eb", "p95": "#dc2626", "p99": "#059669"}
    for index, percentile in enumerate(PERCENTILES):
        points = []
        color = line_colors[percentile]
        for record_index, record in enumerate(records):
            value = float(record.get(f"profiling_server_total_{percentile}_ms") or 0.0)
            x = x_for(record_index)
            y = line_top + line_height - min(value, line_max) / line_max * line_height
            points.append(f"{x:.1f},{y:.1f}")
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')
        if points:
            point_string = " ".join(points)
            lines.append(
                f'<polyline points="{point_string}" fill="none" '
                f'stroke="{color}" stroke-width="2"/>'
            )
        legend_x = left + 170 + index * 120
        lines.append(
            f'<line x1="{legend_x}" y1="{line_top - 25}" '
            f'x2="{legend_x + 18}" y2="{line_top - 25}" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        lines.append(text(legend_x + 24, line_top - 21, percentile.upper(), 11))
    lines.append(text(width / 2, height - 17, "batch_size", 13, "middle"))
    lines.append("</svg>\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate B1 stage profiling results")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    write_outputs(args.root, load_records(args.root))


if __name__ == "__main__":
    main()
