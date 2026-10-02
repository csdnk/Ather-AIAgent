from __future__ import annotations

import os
import re
import secrets
import sys
import time
from typing import Any

import structlog
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aether_agent_memory.runtime.foundation.telemetry import Telemetry


def create_request_logger() -> Any:
    """Create a dedicated one-line JSON logger without changing global logging state."""
    return structlog.wrap_logger(
        structlog.PrintLoggerFactory(file=sys.stdout)(),
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
    )


def configure_tracing(telemetry: Telemetry, endpoint: str | None = None) -> TracerProvider:
    """Connect persistent node spans to an app-local OpenTelemetry provider.

    OTLP export stays off unless an endpoint is supplied in P3 service config or one
    of the standard OTLP/HTTP endpoint variables is present. The HTTP exporter reads
    standard headers, certificates, timeout, and compression variables itself.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": "aether-p3"}))
    traces_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    generic_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    disabled = os.environ.get("OTEL_TRACES_EXPORTER", "").strip().lower() == "none"
    protocol = (
        (
            os.environ.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")
            or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
            or ""
        )
        .strip()
        .lower()
    )
    if protocol and protocol != "http/protobuf":
        provider.shutdown()
        raise ValueError("P3 supports OTLP/HTTP protobuf tracing only")

    exporter: OTLPSpanExporter | None = None
    if not disabled:
        if endpoint:
            exporter = OTLPSpanExporter(endpoint=endpoint)
        elif traces_endpoint or generic_endpoint:
            exporter = OTLPSpanExporter()
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))

    telemetry.set_tracer(provider.get_tracer("aether_agent_memory.runtime"))
    return provider


class RequestObservability:
    """W3C server spans and allowlisted access logs, without body/header capture."""

    def __init__(self, app: ASGIApp, *, telemetry: Telemetry, logger: Any = None) -> None:
        self.app = app
        self.telemetry = telemetry
        self.logger = logger if logger is not None else create_request_logger()
        self.propagator = TraceContextTextMapPropagator()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        tracer = self.telemetry.tracer
        assert tracer is not None
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        request_id = headers.get("x-request-id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            request_id = secrets.token_hex(16)
        method = scope.get("method", "")
        if method not in {
            "GET",
            "HEAD",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
            "CONNECT",
            "TRACE",
        }:
            method = "_OTHER"
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        started = time.monotonic()
        status = 500
        with tracer.start_as_current_span(
            "HTTP",
            context=self.propagator.extract(headers),
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span_context = span.get_span_context()
            trace_id = format(span_context.trace_id, "032x")
            span_id = format(span_context.span_id, "016x")
            state.update(trace_id=trace_id, span_id=span_id)

            async def observed_send(message: Message) -> None:
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                    response_headers = [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() not in {b"x-request-id", b"x-trace-id"}
                    ]
                    response_headers.extend(
                        [
                            (b"x-request-id", request_id.encode("ascii")),
                            (b"x-trace-id", trace_id.encode("ascii")),
                        ]
                    )
                    message = {**message, "headers": response_headers}
                await send(message)

            try:
                await self.app(scope, receive, observed_send)
            except BaseException as exc:
                span.set_attribute("error.type", type(exc).__name__)
                raise
            finally:
                route = getattr(scope.get("route"), "path", None) or "unmatched"
                span.update_name(f"{method} {route}")
                span.set_attributes(
                    {
                        "http.request.method": method,
                        "http.route": route,
                        "http.response.status_code": status,
                        "p3.request_id": request_id,
                    }
                )
                trusted = state.get("context")
                fields: dict[str, Any] = {
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "method": method,
                    "route": route,
                    "status_code": status,
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                }
                if trusted is not None:
                    fields["tenant_id"] = trusted.principal.home_scope.tenant_id
                    fields["principal_id"] = trusted.principal.principal_id
                    fields["operation_id"] = trusted.operation_id
                    span.set_attribute("p3.tenant_id", fields["tenant_id"])
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                try:
                    self.logger.info("http_request", **fields)
                except Exception:
                    # A failed log sink must not turn a completed action into a retry.
                    print('{"level":"ERROR","code":"P3_ACCESS_LOG_WRITE_FAILED"}', file=sys.stderr)
