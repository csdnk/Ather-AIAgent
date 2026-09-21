"""Local structured node logs and spans, independent of business transactions.

No sampling. No body/credential logging. SQLite is the canonical local log store;
authorized queries can be exported as JSONL. This is not an OTLP exporter.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
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

from pydantic import BaseModel

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
                        node.span_id,
                        phase,
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

    @contextmanager
    def span(self, ctx: TrustedContext, name: str, inputs: Any = None) -> Iterator[Node]:
        node = Node(self, ctx, name)
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

    def page(
        self, ctx: TrustedContext, trace_id: str, *, after: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """Internal query: caller MUST revalidate DIAGNOSE before and after reading."""
        if not 1 <= limit <= 500 or after < 0:
            raise ValueError("invalid log page")
        self.prune()
        with self.connect() as db:
            rows = db.execute(
                "SELECT sequence,data FROM node_logs WHERE trace_id=? AND principal_id=? "
                "AND scope=? AND sequence>? ORDER BY sequence LIMIT ?",
                (
                    trace_id,
                    ctx.principal.principal_id,
                    ctx.principal.home_scope.model_dump_json(),
                    after,
                    limit + 1,
                ),
            ).fetchall()
            pruned = db.execute("SELECT value FROM log_meta WHERE key='last_pruned_at'").fetchone()
            states = db.execute(
                "SELECT span_id, MAX(phase='started'), MAX(phase='returned'), "
                "MAX(phase='failed'), MAX(phase='cancelled') FROM node_logs "
                "WHERE trace_id=? AND principal_id=? AND scope=? GROUP BY span_id",
                (trace_id, ctx.principal.principal_id, ctx.principal.home_scope.model_dump_json()),
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
