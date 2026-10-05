"""Structured spans and redaction shared by explicit telemetry backends."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import secrets
import time
import traceback
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
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
    instance_id: str
    dropped: int
    last_error: str | None
    tracer: Tracer | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("Telemetry requires an explicit persistence backend")

    def close(self) -> None:
        raise NotImplementedError

    def reader(self) -> Any:
        raise NotImplementedError

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
        raise NotImplementedError

    def set_tracer(self, tracer: Tracer) -> None:
        self.tracer = tracer

    @contextmanager
    def span(self, ctx: TrustedContext, name: str, inputs: Any = None) -> Iterator[Node]:
        node = Node(self, ctx, name)
        with self._trace_span(node), self._local_span(node, inputs):
            yield node

    @contextmanager
    def _trace_span(self, node: Node) -> Iterator[None]:
        if self.tracer is None:
            yield
            return
        ctx, name = node.ctx, node.name
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
                yield
            except BaseException as exc:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", type(exc).__name__)
                raise

    @asynccontextmanager
    async def async_span(
        self, ctx: TrustedContext, name: str, inputs: Any = None
    ) -> AsyncIterator[Node]:
        """Keep task-local tracing while moving synchronous log I/O off the loop."""
        node = Node(self, ctx, name)
        with self._trace_span(node):
            token = current_node.set(node)
            start = time.monotonic()
            try:
                await self._async_emit(node, "started", input=summary(inputs))
                yield node
            except BaseException as exc:
                await self._async_emit(
                    node,
                    "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                    elapsed_ms=round((time.monotonic() - start) * 1000, 3),
                    error_code=exc.code.value
                    if isinstance(exc, FoundationError)
                    else "DEADLINE_EXCEEDED"
                    if isinstance(exc, TimeoutError)
                    else "NODE_EXCEPTION",
                    error_type=type(exc).__name__,
                    stack=[
                        {"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
                        for f in traceback.extract_tb(exc.__traceback__)[-12:]
                    ],
                )
                raise
            else:
                await self._async_emit(
                    node,
                    "returned",
                    output=summary(node.output),
                    elapsed_ms=round((time.monotonic() - start) * 1000, 3),
                )
            finally:
                current_node.reset(token)

    async def _async_emit(self, node: Node, phase: str, **fields: Any) -> None:
        pending = asyncio.create_task(asyncio.to_thread(self.emit, node, phase, **fields))
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            # Finish the bounded write before emitting cancellation or closing
            # the store, preserving per-span order without blocking heartbeats.
            await asyncio.shield(pending)
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
        raise NotImplementedError

    def page(
        self,
        ctx: TrustedContext,
        trace_id: str,
        *,
        after: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        raise NotImplementedError


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
            async with telemetry.async_span(ctx, name, inputs) as node:
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
