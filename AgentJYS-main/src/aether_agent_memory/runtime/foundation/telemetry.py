"""Local structured node logs and spans, independent of business transactions.

No sampling. No body/credential logging. SQLite is the canonical local log store;
authorized queries can be exported as JSONL. The composition root can attach an
optional OpenTelemetry tracer.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    Status,
    StatusCode,
    TraceFlags,
    Tracer,
)
from pydantic import BaseModel

from aether_agent_memory.runtime.contracts.foundation import NodeLogRecord
from aether_agent_memory.runtime.contracts.models import TrustedContext

from .common import FoundationError, now

# Only these scalar fields may contain clear-text strings. Everything else is
# represented by type/length. In particular exception messages are never stored.
SAFE_FIELDS = frozenset(
    [
        "memory_id",
        "source_id",
        "recall_id",
        "task_id",
        "event_id",
        "action_id",
        "operation_id",
        "request_id",
        "version",
        "revision",
        "stage",
        "state",
        "outcome",
        "effect_status",
        "reason_code",
        "error_code",
        "kind",
        "model_space",
        "model_id",
        "policy_version",
        "coverage",
        "rank",
        "score",
        "dimensions",
        "usage",
        "limit",
        "token_budget",
        "token_count",
        "provider_mode",
        "provider_id",
        "tier",
        "target_tier",
        "attempt",
        "query_attempt",
        "execution_class",
        "consumer_id",
        "worker_id",
        "result_available",
        "readable",
        "searchable",
        "payload_matches",
        "action",
        "mode",
        "status",
        "recovery",
        "selected_count",
        "candidate_count",
        "elapsed_ms",
        "input_hash",
        "content_hash",
        "vector_id",
        "failed",
    ]
)


def summary(value: Any, field: str = "", depth: int = 0) -> Any:
    """Bounded metadata; never serialize arbitrary repr(), messages or raw bodies."""
    if depth > 6:
        return {"truncated": True}
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if field in SAFE_FIELDS:
            return value[:160]
        return {"redacted": True, "characters": len(value)}
    if isinstance(value, dict):
        return {
            str(k)[:80]: summary(v, str(k), depth + 1)
            for k, v in list(value.items())[:40]
            if k not in {"ctx", "principal", "lease", "context"}
        }
    if isinstance(value, (tuple, list)):
        # Vectors and content arrays need only a count, never thousands of scalars.
        return {
            "count": len(value),
            "items": []
            if field in {"vector", "texts"}
            else [summary(v, field, depth + 1) for v in value[:8]],
        }
    return {"type": type(value).__name__}


class Node:
    def __init__(self, store: Telemetry, ctx: TrustedContext, name: str) -> None:
        self.store, self.ctx, self.name = store, ctx, name
        self.span_id = secrets.token_hex(8)
        parent = current_node.get()
        self.parent_id = (
            parent.span_id if parent and parent.ctx.trace_id == ctx.trace_id else ctx.span_id
        )
        self.output: Any = None
        self.started = time.monotonic()


current_node: ContextVar[Node | None] = ContextVar("p3_trace_node", default=None)


class Telemetry:
    def __init__(
        self, path: str | Path, *, retention_days: int = 14, max_records: int = 200_000
    ) -> None:
        if retention_days < 1 or max_records < 100:
            raise ValueError("invalid log retention")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days, self.max_records = retention_days, max_records
        self.instance_id = secrets.token_hex(8)
        self.dropped = 0
        self.last_error: str | None = None
        self._writes = 0
        self.tracer: Tracer | None = None
        self._lock = RLock()
        self._db = sqlite3.connect(self.path, timeout=0.25, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA journal_size_limit=4194304")
        with self.connect() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS node_logs ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, "
                "trace_id TEXT NOT NULL, principal_id TEXT NOT NULL, scope TEXT NOT NULL, "
                "span_id TEXT NOT NULL, phase TEXT NOT NULL, data TEXT NOT NULL);"
                "CREATE INDEX IF NOT EXISTS logs_trace ON node_logs"
                "(trace_id,principal_id,scope,sequence);"
                "CREATE INDEX IF NOT EXISTS logs_age ON node_logs(occurred_at);"
                "CREATE INDEX IF NOT EXISTS logs_sequence ON node_logs(sequence);"
                "CREATE INDEX IF NOT EXISTS logs_catalog ON node_logs"
                "(principal_id,scope,trace_id,sequence,occurred_at,span_id,phase,"
                "json_extract(data,'$.contract.flow'));"
                "CREATE TABLE IF NOT EXISTS log_meta(key TEXT PRIMARY KEY,value TEXT);"
            )
        self.prune()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._db:
            yield self._db

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        # A dashboard query must never hold the writer's Python lock: emitting
        # a span on the asyncio thread would otherwise stall the entire server.
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)
        try:
            db.execute("BEGIN")
            yield db
        finally:
            db.close()

    def retained_bounds(self, db: sqlite3.Connection) -> tuple[int, str]:
        # Enforce retention in the read snapshot, without DELETEs or the write
        # lock. Physical cleanup still runs on startup and every 100 writes.
        row = db.execute(
            "SELECT sequence FROM node_logs ORDER BY sequence DESC LIMIT 1 OFFSET ?",
            (self.max_records,),
        ).fetchone()
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        return (row[0] if row else 0), cutoff

    def prune(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        with self.connect() as db:
            deleted = db.execute("DELETE FROM node_logs WHERE occurred_at < ?", (cutoff,)).rowcount
            deleted += db.execute(
                "DELETE FROM node_logs WHERE sequence <= "
                "(SELECT sequence FROM node_logs ORDER BY sequence DESC LIMIT 1 OFFSET ?)",
                (self.max_records,),
            ).rowcount
            if deleted:
                db.execute("INSERT OR REPLACE INTO log_meta VALUES ('last_pruned_at', ?)", (now(),))

    @staticmethod
    def producer_context(ctx: TrustedContext) -> TrustedContext:
        node = current_node.get()
        if node and node.ctx.trace_id == ctx.trace_id:
            return ctx.model_copy(update={"span_id": node.span_id})
        return ctx

    def emit(self, node: Node, phase: str, **fields: Any) -> None:
        ctx = node.ctx
        data = {
            "schema_version": 1,
            "occurred_at": now(),
            "service": "aether-p3",
            "instance_id": self.instance_id,
            "process_id": os.getpid(),
            "trace_id": ctx.trace_id,
            "span_id": node.span_id,
            "parent_span_id": node.parent_id,
            "request_id": ctx.request_id,
            "operation_id": ctx.operation_id,
            "node": node.name,
            "phase": phase,
            "level": "ERROR"
            if phase == "failed"
            else "WARN"
            if phase in {"cancelled", "rolled_back"}
            else "INFO",
            **fields,
        }
        try:
            flow = node.name.split(".")[0]
            if flow not in {"remember", "recall", "operate"}:
                flow = "runtime"
            normalized = NodeLogRecord(
                occurred_at=data["occurred_at"],
                service="aether-p3",
                instance=self.instance_id,
                flow=flow,
                request_id=ctx.request_id,
                operation_id=ctx.operation_id,
                trace_id=ctx.trace_id,
                span_id=node.span_id,
                parent_span_id=node.parent_id,
                node=re.sub(r"[^A-Za-z0-9_-]", "_", node.name)[:128],
                phase=phase,
                level="error"
                if phase == "failed"
                else "warning"
                if phase in {"cancelled", "rolled_back"}
                else "info",
                elapsed_ms=None
                if phase == "started"
                else max(0, (time.monotonic() - node.started) * 1000),
                reason_code=re.sub(
                    r"[^A-Za-z0-9_-]", "_", str(fields.get("error_code", "NODE_EXCEPTION"))
                )[:128]
                if phase == "failed"
                else None,
                task_id=fields.get("task_id"),
                event_id=fields.get("event_id"),
            )
            data["contract"] = normalized.model_dump(mode="json")
        except ValueError:
            self.dropped += 1
            self.last_error = "InvalidNodeLogRecord"
            return
        self._write(ctx, data)

    def emit_record(self, ctx: TrustedContext, record: NodeLogRecord) -> None:
        record = NodeLogRecord.model_validate_json(record.model_dump_json())
        if (ctx.trace_id, ctx.request_id, ctx.operation_id) != (
            record.trace_id,
            record.request_id,
            record.operation_id,
        ):
            raise ValueError("log context mismatch")
        data = record.model_dump(mode="json")
        self._write(ctx, {**data, "contract": data})

    def _write(self, ctx: TrustedContext, data: dict[str, Any]) -> None:
        try:
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > 32_768:
                data.pop("input", None)
                data.pop("output", None)
                data["detail_truncated"] = True
                encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
            with self.connect() as db:
                db.execute(
                    "INSERT INTO node_logs(occurred_at,trace_id,principal_id,scope,"
                    "span_id,phase,data) VALUES (?,?,?,?,?,?,?)",
                    (
                        data["occurred_at"],
                        ctx.trace_id,
                        ctx.principal.principal_id,
                        ctx.principal.home_scope.model_dump_json(),
                        data["span_id"],
                        data["phase"],
                        encoded,
                    ),
                )
            self._writes += 1
            if self._writes % 100 == 0:
                self.prune()
            self.last_error = None
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            # Never turn a committed external effect into a business retry because
            # logging failed. Health explicitly reports the loss until restart.
            self.dropped += 1
            self.last_error = type(exc).__name__
            if self.dropped == 1:
                print('{"level":"ERROR","code":"P3_LOG_WRITE_FAILED"}', file=sys.stderr)

    def set_tracer(self, tracer: Tracer) -> None:
        self.tracer = tracer

    @contextmanager
    def span(self, ctx: TrustedContext, name: str, inputs: Any = None) -> Iterator[Node]:
        node = Node(self, ctx, name)
        if self.tracer is None:
            with self._local_span(node, inputs):
                yield node
            return
        active = trace.get_current_span().get_span_context()
        if (active.trace_id, active.span_id) == (int(ctx.trace_id, 16), int(node.parent_id, 16)):
            parent_context = None
        else:
            # Durable tasks resume from their trusted producer span, not a random worker span.
            parent = SpanContext(
                int(ctx.trace_id, 16),
                int(node.parent_id, 16),
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
            parent_context = trace.set_span_in_context(NonRecordingSpan(parent), Context())
        with self.tracer.start_as_current_span(
            name,
            context=parent_context,
            record_exception=False,
            set_status_on_exception=False,
            attributes={
                "p3.request_id": ctx.request_id,
                "p3.operation_id": ctx.operation_id,
                "p3.tenant_id": ctx.principal.home_scope.tenant_id,
            },
        ) as span:
            node.span_id = format(span.get_span_context().span_id, "016x")
            try:
                with self._local_span(node, inputs):
                    yield node
            except BaseException as exc:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", type(exc).__name__)
                raise

    @contextmanager
    def _local_span(self, node: Node, inputs: Any) -> Iterator[Node]:
        token = current_node.set(node)
        start = time.monotonic()
        self.emit(node, "started", input=summary(inputs))
        try:
            yield node
        except BaseException as exc:
            self.emit(
                node,
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                elapsed_ms=round((time.monotonic() - start) * 1000, 3),
                error_code=exc.code.value
                if isinstance(exc, FoundationError)
                else "DEADLINE_EXCEEDED"
                if isinstance(exc, TimeoutError)
                else "NODE_EXCEPTION",
                error_type=type(exc).__name__,
                # Stack positions, without source lines, locals, or exception text.
                stack=[
                    {"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
                    for f in traceback.extract_tb(exc.__traceback__)[-12:]
                ],
            )
            raise
        else:
            self.emit(
                node,
                "returned",
                output=summary(node.output),
                elapsed_ms=round((time.monotonic() - start) * 1000, 3),
            )
        finally:
            current_node.reset(token)

    def traces(
        self,
        ctx: TrustedContext,
        *,
        before: int | None = None,
        limit: int = 50,
        flow: str | None = None,
    ) -> dict[str, Any]:
        """Retained trace metadata only; caller must authorize before and after reading.

        Order by first retained sequence, not changing activity time. Keyset paging
        excludes newly arriving traces from subsequent pages. Retention can still
        remove records; this catalog never claims completeness or business success.
        """
        if not 1 <= limit <= 100 or (before is not None and before < 1):
            raise ValueError("invalid trace page")
        if flow not in {None, "business", "remember", "recall", "operate", "runtime"}:
            raise ValueError("invalid trace flow")
        with self.reader() as db:
            floor, cutoff = self.retained_bounds(db)
            rows = db.execute(
                "SELECT trace_id, MIN(sequence), MIN(occurred_at), MAX(occurred_at), "
                "COUNT(*), COUNT(DISTINCT span_id), "
                "COUNT(DISTINCT CASE WHEN phase='failed' THEN span_id END) "
                "FROM node_logs WHERE principal_id=? AND scope=? "
                "AND sequence>? AND occurred_at>=? "
                "GROUP BY trace_id HAVING (? IS NULL OR MIN(sequence) < ?) "
                "AND (? IS NULL OR MAX(CASE WHEN "
                "json_extract(data,'$.contract.flow') = ? OR "
                "(?='business' AND json_extract(data,'$.contract.flow') "
                "IN ('remember','recall','operate')) THEN 1 ELSE 0 END)=1) "
                "ORDER BY MIN(sequence) DESC LIMIT ?",
                (
                    ctx.principal.principal_id,
                    ctx.principal.home_scope.model_dump_json(),
                    floor,
                    cutoff,
                    before,
                    before,
                    flow,
                    flow,
                    flow,
                    limit + 1,
                ),
            ).fetchall()
            items = []
            for trace_id, first, started, last, records, spans, failed in rows[:limit]:
                raw = db.execute("SELECT data FROM node_logs WHERE sequence=?", (first,)).fetchone()
                contract = json.loads(raw[0]).get("contract", {})
                items.append(
                    {
                        "trace_id": trace_id,
                        "first_sequence": first,
                        "started_at": started,
                        "last_seen": last,
                        "record_count": records,
                        "span_count": spans,
                        "failed_span_count": failed,
                        "entry_node": contract.get("node", "unknown"),
                        "flow": contract.get("flow", "runtime"),
                    }
                )
        return {
            "items": items,
            "next_before": rows[limit - 1][1] if len(rows) > limit else None,
            "coverage": "retained_records_only",
        }

    def page(
        self, ctx: TrustedContext, trace_id: str, *, after: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """Internal query: caller MUST revalidate DIAGNOSE before and after reading."""
        if not 1 <= limit <= 500 or after < 0:
            raise ValueError("invalid log page")
        with self.reader() as db:
            floor, cutoff = self.retained_bounds(db)
            rows = db.execute(
                "SELECT sequence,data FROM node_logs WHERE trace_id=? AND principal_id=? "
                "AND scope=? AND sequence>? AND occurred_at>=? ORDER BY sequence LIMIT ?",
                (
                    trace_id,
                    ctx.principal.principal_id,
                    ctx.principal.home_scope.model_dump_json(),
                    max(after, floor),
                    cutoff,
                    limit + 1,
                ),
            ).fetchall()
            pruned = db.execute("SELECT value FROM log_meta WHERE key='last_pruned_at'").fetchone()
            states = db.execute(
                "SELECT span_id, MAX(phase='started'), MAX(phase='returned'), "
                "MAX(phase='failed'), MAX(phase='cancelled') FROM node_logs "
                "WHERE trace_id=? AND principal_id=? AND scope=? "
                "AND sequence>? AND occurred_at>=? GROUP BY span_id",
                (
                    trace_id,
                    ctx.principal.principal_id,
                    ctx.principal.home_scope.model_dump_json(),
                    floor,
                    cutoff,
                ),
            ).fetchall()
        open_spans = [s[0] for s in states if s[1] and not any(s[2:])]
        return {
            "trace_id": trace_id,
            "records": [{"sequence": seq, **json.loads(raw)} for seq, raw in rows[:limit]],
            "next_after": rows[limit - 1][0] if len(rows) > limit else None,
            "coverage": "retained_records_only",
            "last_pruned_at": pruned[0] if pruned else None,
            "local_dropped_records": self.dropped,
            "overview": {
                "span_count": len(states),
                "failed_span_count": sum(bool(s[3]) for s in states),
                "open_span_count": len(open_spans),
                "open_span_ids": open_spans[:100],
                "open_meaning": "running_or_interrupted; inspect durable task state",
            },
        }


T = TypeVar("T", bound=type[Any])


def observed(component: str) -> Callable[[T], T]:
    """Instrument explicit TrustedContext methods, preserving their public types.

    Host installs telemetry on providers; services obtain it from their UoW.
    Decorates class functions, so registered bound task handlers are covered too.
    """

    def decorate(cls: T) -> T:
        for name, function in list(vars(cls).items()):
            if inspect.isfunction(function) and "ctx" in inspect.signature(function).parameters:
                setattr(cls, name, _wrap(function, component + "." + name))
        return cls

    return decorate


def _wrap(function: Any, name: str) -> Any:
    signature = inspect.signature(function)

    def context(args: Any, kwargs: Any) -> tuple[Telemetry | None, Any, Any]:
        bound = signature.bind(*args, **kwargs).arguments
        owner = bound.get("self")
        telemetry = getattr(owner, "_telemetry", None) or getattr(
            getattr(owner, "uow", None), "telemetry", None
        )
        return (
            telemetry,
            bound.get("ctx"),
            {k: v for k, v in bound.items() if k not in {"self", "ctx", "tx", "sql"}},
        )

    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_call(*args: Any, **kwargs: Any) -> Any:
            telemetry, ctx, inputs = context(args, kwargs)
            if telemetry is None:
                return await function(*args, **kwargs)
            with telemetry.span(ctx, name, inputs) as node:
                node.output = await function(*args, **kwargs)
                return node.output

        return async_call

    @wraps(function)
    def sync_call(*args: Any, **kwargs: Any) -> Any:
        telemetry, ctx, inputs = context(args, kwargs)
        if telemetry is None:
            return function(*args, **kwargs)
        with telemetry.span(ctx, name, inputs) as node:
            node.output = function(*args, **kwargs)
            return node.output

    return sync_call


def attach_provider(provider: Any, telemetry: Telemetry, component: str) -> None:
    """Instrument injected adapters without requiring a new business port."""
    provider._telemetry = telemetry
    # Built-ins already carry class decorators. External adapters are wrapped
    # only on this instance, leaving other hosts and provider classes untouched.
    for name in (
        "extract",
        "embed",
        "search",
        "project",
        "inspect",
        "delete",
        "submit",
        "query",
        "observe",
        "resources",
        "verify_read",
        "rerank",
    ):
        method = getattr(provider, name, None)
        if method is not None and not hasattr(method, "__wrapped__"):
            function = getattr(type(provider), name, None)
            if function and "ctx" in inspect.signature(function).parameters:
                setattr(provider, name, _wrap(function, component + "." + name).__get__(provider))
