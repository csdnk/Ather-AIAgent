"""Bounded, read-only observations of successful provider calls, not capacity tests."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

_LIMIT = 10_000
_WINDOW_SECONDS = 86_400
_NODES = ("embedding.native.embed", "embedding.embed", "remember.read_body")
_EMBEDDING_PHASES = (
    "embedding_prepare",
    "embedding_compute",
    "embedding_evidence",
    "embedding_finalize",
)
_MEMORY_PHASES = ("memory_prepare", "memory_fetch", "memory_validate")
_SHARED_PHASES = ("postgres_local_wait", "postgres_lock_wait", "postgres_transaction", "log_write")
_PHASES = (*_EMBEDDING_PHASES, *_MEMORY_PHASES, *_SHARED_PHASES)
_COLUMNS = (
    "occurred_at",
    "node",
    "elapsed_json",
    "count_json",
    "is_health",
    "memory_id",
    "outcome",
    *(f"{phase}_{kind}" for phase in _PHASES for kind in ("ms", "count")),
)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _query(postgres: bool) -> str:
    """Project only known scalar fields; never transfer log bodies or vectors."""
    if postgres:
        document = "data::jsonb"

        def scalar(path: tuple[str, ...], kind: str) -> str:
            expression = document + " #> '{" + ",".join(path) + "}'"
            text = document + " #>> '{" + ",".join(path) + "}'"
            return f"CASE WHEN jsonb_typeof({expression})='{kind}' THEN {text} END"

        marker = "%s"
    else:
        document = "(CASE WHEN json_valid(data) THEN data ELSE '{}' END)"

        def scalar(path: tuple[str, ...], kind: str) -> str:
            pointer = "$." + ".".join(path)
            kinds = "'integer','real'" if kind == "number" else "'text'"
            # -> keeps the numeric JSON spelling, unlike json_extract's lossy coercion.
            text = (
                f"{document} -> '{pointer}'"
                if kind == "number"
                else f"json_extract({document},'{pointer}')"
            )
            return f"CASE WHEN json_type({document},'{pointer}') IN ({kinds}) THEN {text} END"

        marker = "?"
    node = scalar(("node",), "string")
    elapsed = scalar(("elapsed_ms",), "number")
    count = scalar(("output", "items", "count"), "number")
    operation = scalar(("output", "operation_id"), "string")
    memory = scalar(("output", "memory", "memory_id"), "string")
    outcome = scalar(("output", "outcome"), "string")
    timing_columns = []
    for phase in _PHASES:
        for key, suffix in (("timings_ms", "ms"), ("timing_counts", "count")):
            value = scalar((key, phase), "number")
            timing_columns.append(
                f"CASE WHEN length({value})<=256 THEN {value} END AS {phase}_{suffix}"
            )
    return (
        f"SELECT occurred_at, {node} AS node, {elapsed} AS elapsed_json, "
        f"{count} AS count_json, CASE WHEN substr({operation},1,7)='health_' "
        f"THEN 1 ELSE 0 END AS is_health, "
        f"CASE WHEN length({memory})<=160 THEN {memory} END AS memory_id, "
        f"CASE WHEN {outcome}='read' THEN 'read' END AS outcome, " + ", ".join(timing_columns) + " "
        f"FROM node_logs WHERE occurred_at >= {marker} AND occurred_at <= {marker} "
        f"AND phase='returned' AND {node} IN ({','.join([marker] * len(_NODES))}) "
        "ORDER BY occurred_at DESC, sequence DESC LIMIT 10001"
    )


def _number(value: Any) -> int | float | None:
    if not isinstance(value, str) or len(value) > 256:
        return None
    try:
        number = json.loads(value)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            return None
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, OverflowError):
        return None


def _phase_breakdown(phases: tuple[str, ...]) -> dict[str, Any]:
    return {
        "timed_samples": 0,
        "missing_samples": 0,
        "inclusive": True,
        "excludes_terminal_write": True,
        "percentile_method": "nearest_rank",
        "items": [
            {"phase": phase, "samples": 0, "avg_ms": None, "p95_ms": None, "calls": None}
            for phase in phases
        ],
    }


def _collect_phases(row: dict[str, Any], breakdown: dict[str, Any], values: dict) -> None:
    observed = False
    for item in breakdown["items"]:
        phase = item["phase"]
        duration = _number(row.get(f"{phase}_ms"))
        if duration is None or duration > 2**53 - 1:
            continue
        observed = True
        values.setdefault(phase, []).append(float(duration))
        count = _number(row.get(f"{phase}_count"))
        if isinstance(count, int) and 0 < count <= 2**53 - 1:
            item["calls"] = (item["calls"] or 0) + count
    breakdown["timed_samples" if observed else "missing_samples"] += 1


def _finish_phases(breakdown: dict[str, Any], values: dict) -> None:
    for item in breakdown["items"]:
        durations = sorted(values.get(item["phase"], []))
        if durations:
            item.update(
                samples=len(durations),
                avg_ms=sum(durations) / len(durations),
                p95_ms=durations[math.ceil(0.95 * len(durations)) - 1],
            )


def performance_observations(
    telemetry: Any, observed_at: str, *, working_memory_ids: set[str] | None = None
) -> dict[str, Any]:
    """Caller authenticates platform access and supplies verified Working Memory IDs.

    Both API and workers must write to this telemetry database. Rates describe
    recorded calls in this database, without assuming logs are lossless. Working
    body latency includes authorization, storage and result revalidation, but not
    vector retrieval or generation. The nearest-rank P99 uses successful reads.
    """
    end = datetime.fromisoformat(observed_at)
    if end.tzinfo is None:
        raise ValueError("performance observation time must include a timezone")
    end = end.astimezone(UTC)
    start = end - timedelta(seconds=_WINDOW_SECONDS)
    common = {
        "status": "no_samples",
        "window_seconds": _WINDOW_SECONDS,
        "from": _stamp(start),
        "to": _stamp(end),
        "observed_at": _stamp(end),
        "last_observed": None,
        "truncated": False,
        "coverage": "persisted_calls_in_current_database",
    }
    embedding: dict[str, Any] = {
        **common,
        "rate": None,
        "batch_count": 0,
        "items": 0,
        "elapsed_ms": None,
        "phase_breakdown": _phase_breakdown((*_EMBEDDING_PHASES, *_SHARED_PHASES)),
        "scope": "successful vectors / summed successful batch elapsed seconds; "
        "not wall-clock throughput or capacity benchmark",
    }
    working: dict[str, Any] = {
        **common,
        "p99_ms": None,
        "samples": 0,
        "phase_breakdown": _phase_breakdown((*_MEMORY_PHASES, *_SHARED_PHASES)),
        "percentile_method": "nearest_rank",
        "scope": "successful Working Memory full body reads including authorization, "
        "storage and revalidation; excludes vector search and generation",
    }
    result = {"embedding": embedding, "working_memory": working}
    try:
        with telemetry.reader() as db:
            rows = db.execute(
                _query(getattr(telemetry, "backend", None) == "postgresql"),
                (_stamp(start), _stamp(end), *_NODES),
            ).fetchall()
    except Exception:
        # The dashboard cannot turn a telemetry outage into a business failure or
        # expose provider exception strings, which may contain database secrets.
        embedding["status"] = working["status"] = "unavailable"
        return result

    truncated = len(rows) > _LIMIT
    embedding["truncated"] = working["truncated"] = truncated
    latencies: list[float] = []
    durations: list[float] = []
    vectors = 0
    embedding_phases: dict[str, list[float]] = {}
    memory_phases: dict[str, list[float]] = {}
    for raw in rows[:_LIMIT]:
        row = dict(raw) if hasattr(raw, "keys") else dict(zip(_COLUMNS, raw, strict=True))
        if row["is_health"]:
            continue
        elapsed = _number(row["elapsed_json"])
        if elapsed is None:
            continue
        try:
            occurred = datetime.fromisoformat(row["occurred_at"])
            if occurred.tzinfo is None or not start <= occurred <= end:
                continue
        except (TypeError, ValueError):
            continue
        stamp = _stamp(occurred)
        if row["node"] in _NODES[:2]:
            count = _number(row["count_json"])
            if not isinstance(count, int) or count <= 0 or count > 2**53 - 1 or elapsed <= 0:
                continue
            vectors += count
            durations.append(float(elapsed))
            embedding["last_observed"] = max(embedding["last_observed"] or stamp, stamp)
            _collect_phases(row, embedding["phase_breakdown"], embedding_phases)
        elif (
            row["outcome"] == "read"
            and isinstance(row["memory_id"], str)
            and row["memory_id"] in (working_memory_ids or set())
        ):
            latencies.append(float(elapsed))
            working["last_observed"] = max(working["last_observed"] or stamp, stamp)
            _collect_phases(row, working["phase_breakdown"], memory_phases)
    _finish_phases(embedding["phase_breakdown"], embedding_phases)
    _finish_phases(working["phase_breakdown"], memory_phases)
    if durations:
        elapsed_sum = sum(durations)
        rate = vectors / elapsed_sum * 1000
        embedding.update(
            status="available"
            if math.isfinite(elapsed_sum) and math.isfinite(rate)
            else "unavailable",
            rate=rate if math.isfinite(elapsed_sum) and math.isfinite(rate) else None,
            batch_count=len(durations),
            items=vectors,
            elapsed_ms=elapsed_sum if math.isfinite(elapsed_sum) else None,
        )
    if latencies:
        latencies.sort()
        working.update(
            status="available",
            samples=len(latencies),
            p99_ms=latencies[math.ceil(0.99 * len(latencies)) - 1],
        )
    if truncated:
        for metric in result.values():
            if metric["status"] != "unavailable":
                metric["status"] = "partial"
    return result
