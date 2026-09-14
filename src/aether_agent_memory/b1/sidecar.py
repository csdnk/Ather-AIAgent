"""A real CPU Embedding Sidecar for the P3-B1 delivery boundary."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import sys
import time
import uuid
from collections import Counter, OrderedDict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aether_agent_memory.b1.backends import (
    DEFAULT_MODEL_BATCH_SIZE,
    BackendConfig,
    BackendUnavailableError,
    SidecarEmbeddingBackend,
    create_backend_chain,
    describe_backends,
)
from aether_agent_memory.b1.batching import (
    BatchPolicy,
    DynamicBatchClosed,
    DynamicBatchQueueFull,
    DynamicBatchScheduler,
    DynamicBatchTimeout,
    EmbeddingJobResult,
    estimate_tokens,
)
from aether_agent_memory.b1.capabilities import detect_runtime_capabilities

MODEL_NAME = "BAAI/bge-small-zh-v1.5"


def _configure_utf8_stdio() -> None:
    """Keep third-party model/export progress logs safe on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_configure_utf8_stdio()


def _env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _env_nonnegative_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _env_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(value.strip().lower() for value in raw.split(",") if value.strip())


def _env_int_tuple(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = tuple(int(value.strip()) for value in raw.split(",") if value.strip())
    if not values or any(value <= 0 for value in values):
        raise ValueError(f"{name} must contain positive integers")
    return tuple(sorted(values))


def _model_path() -> Path | None:
    value = os.getenv("AETHER_B1_MODEL_PATH")
    return Path(value) if value else None


@dataclass(frozen=True)
class SidecarSettings:
    backend_name: str = field(default_factory=lambda: os.getenv("AETHER_B1_BACKEND", "onnx"))
    precision: Literal["fp32", "int8"] = field(
        default_factory=lambda: cast(
            Literal["fp32", "int8"],
            os.getenv("AETHER_B1_PRECISION", "fp32"),
        )
    )
    fallback_backends: tuple[str, ...] = field(
        default_factory=lambda: _env_csv("AETHER_B1_FALLBACK_BACKENDS", ("onnx",))
    )
    allow_backend_fallback: bool = field(
        default_factory=lambda: _env_bool("AETHER_B1_ALLOW_BACKEND_FALLBACK", True)
    )
    model_name: str = field(default_factory=lambda: os.getenv("AETHER_B1_MODEL_NAME", MODEL_NAME))
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("AETHER_B1_CACHE_DIR", str(Path.cwd() / ".aether" / "b1" / "models"))
        )
    )
    model_path: Path | None = field(default_factory=_model_path)
    host: str = field(default_factory=lambda: os.getenv("AETHER_B1_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("AETHER_B1_PORT", 18081))
    threads: int = field(default_factory=lambda: _env_int("AETHER_B1_THREADS", 1))
    inter_op_threads: int = field(default_factory=lambda: _env_int("AETHER_B1_INTER_OP_THREADS", 1))
    max_length: int = field(default_factory=lambda: _env_int("AETHER_B1_MAX_LENGTH", 512))
    enable_simd: bool = field(default_factory=lambda: _env_bool("AETHER_B1_SIMD_ENABLED", True))
    profile_timing: bool = field(
        default_factory=lambda: _env_bool("AETHER_B1_PROFILE_TIMING", False)
    )
    model_batch_size: int = field(
        default_factory=lambda: _env_int("AETHER_B1_MODEL_BATCH_SIZE", DEFAULT_MODEL_BATCH_SIZE)
    )
    max_batch_items: int = field(default_factory=lambda: _env_int("AETHER_B1_MAX_BATCH_ITEMS", 32))
    dynamic_batching: bool = field(
        default_factory=lambda: _env_bool("AETHER_B1_DYNAMIC_BATCHING", False)
    )
    dynamic_max_batch_items: int = field(
        default_factory=lambda: _env_int("AETHER_B1_DYNAMIC_MAX_BATCH_ITEMS", 64)
    )
    dynamic_max_batch_tokens: int = field(
        default_factory=lambda: _env_int("AETHER_B1_DYNAMIC_MAX_BATCH_TOKENS", 8192)
    )
    dynamic_max_wait_ms: float = field(
        default_factory=lambda: _env_float("AETHER_B1_DYNAMIC_MAX_WAIT_MS", 2.0)
    )
    dynamic_queue_size: int = field(
        default_factory=lambda: _env_int("AETHER_B1_DYNAMIC_QUEUE_SIZE", 4096)
    )
    dynamic_length_buckets: tuple[int, ...] = field(
        default_factory=lambda: _env_int_tuple("AETHER_B1_LENGTH_BUCKETS", (32, 64, 128, 256, 512))
    )
    max_body_bytes: int = field(
        default_factory=lambda: _env_int("AETHER_B1_MAX_BODY_BYTES", 2 * 1024 * 1024)
    )
    max_input_chars: int = field(
        default_factory=lambda: _env_int("AETHER_B1_MAX_INPUT_CHARS", 32768)
    )
    chunk_max_chars: int = field(default_factory=lambda: _env_int("AETHER_B1_CHUNK_MAX_CHARS", 400))
    chunk_overlap_chars: int = field(
        default_factory=lambda: _env_int("AETHER_B1_CHUNK_OVERLAP_CHARS", 40)
    )
    max_chunks_per_item: int = field(
        default_factory=lambda: _env_int("AETHER_B1_MAX_CHUNKS_PER_ITEM", 96)
    )
    max_metadata_bytes: int = field(
        default_factory=lambda: _env_int("AETHER_B1_MAX_METADATA_BYTES", 16384)
    )
    max_concurrency: int = field(default_factory=lambda: _env_int("AETHER_B1_MAX_CONCURRENCY", 1))
    queue_timeout_seconds: float = field(
        default_factory=lambda: _env_float("AETHER_B1_QUEUE_TIMEOUT_SECONDS", 5.0)
    )
    backend_timeout_seconds: float = field(
        default_factory=lambda: _env_float("AETHER_B1_BACKEND_TIMEOUT_SECONDS", 120.0)
    )
    idempotency_cache_size: int = field(
        default_factory=lambda: _env_int("AETHER_B1_IDEMPOTENCY_CACHE_SIZE", 1024)
    )
    event_history_size: int = field(
        default_factory=lambda: _env_int("AETHER_B1_EVENT_HISTORY_SIZE", 200)
    )
    metrics_window_seconds: int = field(
        default_factory=lambda: _env_int("AETHER_B1_METRICS_WINDOW_SECONDS", 60)
    )
    fail_mode: Literal["open", "closed"] = field(
        default_factory=lambda: cast(
            Literal["open", "closed"],
            os.getenv("AETHER_B1_FAIL_MODE", "open"),
        )
    )
    eager_load: bool = field(default_factory=lambda: _env_bool("AETHER_B1_EAGER_LOAD", True))
    openvino_async: bool = field(
        default_factory=lambda: _env_bool("AETHER_B1_OPENVINO_ASYNC", False)
    )
    openvino_infer_requests: int = field(
        default_factory=lambda: _env_nonnegative_int("AETHER_B1_OPENVINO_INFER_REQUESTS", 0)
    )
    openvino_num_streams: str = field(
        default_factory=lambda: os.getenv("AETHER_B1_OPENVINO_NUM_STREAMS", "AUTO")
    )

    def __post_init__(self) -> None:
        positive_fields = {
            "port": self.port,
            "threads": self.threads,
            "inter_op_threads": self.inter_op_threads,
            "max_length": self.max_length,
            "model_batch_size": self.model_batch_size,
            "max_batch_items": self.max_batch_items,
            "dynamic_max_batch_items": self.dynamic_max_batch_items,
            "dynamic_max_batch_tokens": self.dynamic_max_batch_tokens,
            "dynamic_max_wait_ms": self.dynamic_max_wait_ms,
            "dynamic_queue_size": self.dynamic_queue_size,
            "max_body_bytes": self.max_body_bytes,
            "max_input_chars": self.max_input_chars,
            "chunk_max_chars": self.chunk_max_chars,
            "max_chunks_per_item": self.max_chunks_per_item,
            "max_metadata_bytes": self.max_metadata_bytes,
            "max_concurrency": self.max_concurrency,
            "queue_timeout_seconds": self.queue_timeout_seconds,
            "backend_timeout_seconds": self.backend_timeout_seconds,
            "idempotency_cache_size": self.idempotency_cache_size,
            "event_history_size": self.event_history_size,
            "metrics_window_seconds": self.metrics_window_seconds,
        }
        invalid = [name for name, value in positive_fields.items() if value <= 0]
        if invalid:
            raise ValueError(f"B1 settings must be positive: {', '.join(invalid)}")
        if self.chunk_overlap_chars < 0:
            raise ValueError("AETHER_B1_CHUNK_OVERLAP_CHARS must not be negative")
        if self.chunk_overlap_chars >= self.chunk_max_chars:
            raise ValueError(
                "AETHER_B1_CHUNK_OVERLAP_CHARS must be less than AETHER_B1_CHUNK_MAX_CHARS"
            )
        if self.fail_mode not in {"open", "closed"}:
            raise ValueError("AETHER_B1_FAIL_MODE must be open or closed")
        if self.precision not in {"fp32", "int8"}:
            raise ValueError("AETHER_B1_PRECISION must be fp32 or int8")
        if not self.backend_name.strip():
            raise ValueError("AETHER_B1_BACKEND must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("AETHER_B1_PORT must be between 1 and 65535")

    def backend_config(self) -> BackendConfig:
        return BackendConfig(
            model_name=self.model_name,
            cache_dir=self.cache_dir,
            model_path=self.model_path,
            threads=self.threads,
            model_batch_size=self.model_batch_size,
            precision=self.precision,
            max_length=self.max_length,
            inter_op_threads=self.inter_op_threads,
            enable_simd=self.enable_simd,
            openvino_async=self.openvino_async,
            openvino_infer_requests=self.openvino_infer_requests,
            openvino_num_streams=self.openvino_num_streams,
        )

    def batch_policy(self) -> BatchPolicy:
        return BatchPolicy(
            max_batch_items=self.dynamic_max_batch_items,
            max_batch_tokens=self.dynamic_max_batch_tokens,
            max_wait_ms=self.dynamic_max_wait_ms,
            queue_size=self.dynamic_queue_size,
            length_buckets=self.dynamic_length_buckets,
            backend_timeout_seconds=self.backend_timeout_seconds,
        )


class InterceptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=256)
    object_id: str | None = Field(default=None, max_length=256)
    chunk_id: str | None = Field(default=None, max_length=256)
    text: str | None = None
    chunk_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding_required: bool = True
    input_type: Literal["passage", "query"] = "passage"
    preserve_input: bool = False

    @model_validator(mode="after")
    def exactly_one_text_field(self) -> InterceptItem:
        supplied = [value is not None for value in (self.text, self.chunk_text)]
        if sum(supplied) != 1:
            raise ValueError("exactly one of text or chunk_text is required")
        required_identifiers = {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
        }
        if any(not value.strip() for value in required_identifiers.values()):
            raise ValueError("request_id, tenant_id, source_type and source_id must not be blank")
        return self

    @property
    def content(self) -> str:
        return self.text if self.text is not None else self.chunk_text or ""


class _UnavailableBackend:
    engine_name = "unavailable"
    dimension: int | None = None
    device = "CPU"
    precision = "unknown"
    model_hash: str | None = None

    def __init__(self, key: str, model_name: str, error: str) -> None:
        self.backend_key = key
        self.model_name = model_name
        self.error = error

    def load(self) -> None:
        raise BackendUnavailableError(self.error)

    def embed(
        self,
        texts: list[str],
        input_types: list[str],
        batch_size: int,
    ) -> list[np.ndarray]:
        raise BackendUnavailableError(self.error)

    def runtime_details(self) -> dict[str, Any]:
        return {"backend_key": self.backend_key, "engine": self.engine_name, "error": self.error}


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    remainder = position - lower
    return ordered[lower] * (1 - remainder) + ordered[upper] * remainder


class Metrics:
    def __init__(self, window_seconds: int, history_size: int) -> None:
        self.started_at = time.time()
        self.window_seconds = window_seconds
        self.requests = 0
        self.items = 0
        self.success = 0
        self.skipped = 0
        self.failed = 0
        self.replayed = 0
        self.backend_failures = 0
        self.backend_timeouts = 0
        self.queue_timeouts = 0
        self.total_latency_ms = 0.0
        self.total_input_chars = 0
        self.total_chunks = 0
        self.total_vectors = 0
        self.error_codes: Counter[str] = Counter()
        self._recent: deque[dict[str, Any]] = deque(maxlen=max(history_size * 10, 1000))

    def begin(self, item_count: int) -> None:
        self.requests += 1
        self.items += item_count

    def finish(self, results: list[dict[str, Any]], latency_ms: float) -> None:
        self.total_latency_ms += latency_ms
        counts = Counter(str(result.get("status", "failed")) for result in results)
        self.success += counts["success"]
        self.skipped += counts["skipped"]
        self.failed += counts["failed"]
        self.replayed += sum(bool(result.get("idempotent_replay")) for result in results)
        self.total_input_chars += sum(int(result.get("input_chars", 0)) for result in results)
        self.total_chunks += sum(int(result.get("chunk_count", 0)) for result in results)
        self.total_vectors += sum(int(result.get("vector_count", 0)) for result in results)
        for result in results:
            if code := result.get("error_code"):
                self.error_codes[str(code)] += 1
        self._recent.append(
            {
                "timestamp": time.time(),
                "items": len(results),
                "success": counts["success"],
                "skipped": counts["skipped"],
                "failed": counts["failed"],
                "vectors": sum(int(result.get("vector_count", 0)) for result in results),
                "latency_ms": latency_ms,
            }
        )

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        cutoff = now - self.window_seconds
        recent = [record for record in self._recent if record["timestamp"] >= cutoff]
        elapsed = max(min(now - self.started_at, self.window_seconds), 1.0)
        latencies = [float(record["latency_ms"]) for record in recent]
        recent_items = sum(int(record["items"]) for record in recent)
        recent_success = sum(int(record["success"]) for record in recent)
        recent_skipped = sum(int(record["skipped"]) for record in recent)
        recent_failed = sum(int(record["failed"]) for record in recent)
        recent_total = recent_success + recent_skipped + recent_failed
        request_qps = len(recent) / elapsed
        effective_item_qps = recent_success / elapsed
        vector_qps = (
            sum(int(record.get("vectors", 0)) for record in recent) / elapsed if recent else 0.0
        )
        return {
            "started_at_epoch": self.started_at,
            "uptime_seconds": round(now - self.started_at, 3),
            "requests": self.requests,
            "items": self.items,
            "success": self.success,
            "skipped": self.skipped,
            "failed": self.failed,
            "success_rate": round(self.success / self.items if self.items else 0.0, 6),
            "idempotent_replays": self.replayed,
            "backend_failures": self.backend_failures,
            "backend_timeouts": self.backend_timeouts,
            "queue_timeouts": self.queue_timeouts,
            "total_input_chars": self.total_input_chars,
            "total_chunks": self.total_chunks,
            "total_vectors": self.total_vectors,
            "average_request_latency_ms": round(
                self.total_latency_ms / self.requests if self.requests else 0.0, 3
            ),
            "error_codes": dict(self.error_codes),
            "window_seconds": self.window_seconds,
            "window_requests": len(recent),
            "window_items": recent_items,
            "requests_per_second": round(request_qps, 3),
            "items_per_second": round(recent_items / elapsed, 3),
            "http_request_qps": round(request_qps, 3),
            "effective_item_qps": round(effective_item_qps, 3),
            "vector_qps": round(vector_qps, 3),
            "window_success_rate": round(recent_success / recent_total if recent_total else 0.0, 6),
            "request_latency_p50_ms": round(_percentile(latencies, 0.50), 3),
            "request_latency_p95_ms": round(_percentile(latencies, 0.95), 3),
            "request_latency_p99_ms": round(_percentile(latencies, 0.99), 3),
        }


def _natural_chunks(text: str, max_chars: int, overlap_chars: int) -> list[tuple[int, int, str]]:
    left_trim = len(text) - len(text.lstrip())
    right_limit = len(text.rstrip())
    if left_trim >= right_limit:
        return []
    working = text[left_trim:right_limit]
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(working):
        hard_end = min(start + max_chars, len(working))
        end = hard_end
        if hard_end < len(working):
            search_start = start + (hard_end - start) // 2
            boundary = max(
                working.rfind(mark, search_start, hard_end)
                for mark in ("\n", ".", "!", "?", "。", "！", "？", "，", ",", " ")
            )
            if boundary >= search_start:
                end = boundary + 1
        global_start = left_trim + start
        global_end = left_trim + end
        raw = text[global_start:global_end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        chunk_start = global_start + leading
        chunk_end = global_start + trailing
        if chunk_start < chunk_end:
            chunks.append((chunk_start, chunk_end, text[chunk_start:chunk_end]))
        if end >= len(working):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _digest(item: InterceptItem) -> str:
    payload = item.model_dump(mode="json", exclude={"trace_id"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _raw_input_chars(raw: Any) -> int:
    if not isinstance(raw, dict):
        return 0
    value = raw.get("text", raw.get("chunk_text", ""))
    return len(value) if isinstance(value, str) else 0


def _error_result(
    raw: Any,
    code: str,
    message: str,
    trace_id: str | None = None,
) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    return {
        "request_id": item.get("request_id"),
        "trace_id": trace_id or item.get("trace_id") or uuid.uuid4().hex,
        "tenant_id": item.get("tenant_id"),
        "source_type": item.get("source_type"),
        "source_id": item.get("source_id"),
        "status": "failed",
        "error_code": code,
        "error_message": message[:1000],
        "latency_ms": 0.0,
        "embedding_latency_ms": 0.0,
        "input_chars": _raw_input_chars(raw),
        "chunk_count": 0,
        "vector_count": 0,
        "chunks": [],
    }


def _failure_stage(error_code: str | None) -> str:
    if error_code in {
        "B1_INVALID_REQUEST",
        "B1_EMPTY_TEXT",
        "B1_TEXT_TOO_LONG",
        "B1_METADATA_TOO_LARGE",
        "B1_TOO_MANY_CHUNKS",
        "B1_IDEMPOTENCY_CONFLICT",
    }:
        return "validation"
    if error_code == "B1_BUSY":
        return "queue"
    if error_code == "B1_EMBEDDING_TIMEOUT":
        return "embedding-timeout"
    if error_code == "B1_MODEL_NOT_READY":
        return "backend-load"
    return "embedding"


class B1Service:
    def __init__(
        self,
        settings: SidecarSettings,
        backend: SidecarEmbeddingBackend,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.metrics = Metrics(settings.metrics_window_seconds, settings.event_history_size)
        self.ready = False
        self.load_error: str | None = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._load_lock = asyncio.Lock()
        self._cache: OrderedDict[tuple[str, str], tuple[str, dict[str, Any]]] = OrderedDict()
        self._inflight: dict[tuple[str, str], tuple[str, asyncio.Future[dict[str, Any]]]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=settings.event_history_size)
        self._event_sequence = 0
        self._batch_scheduler: DynamicBatchScheduler | None = (
            DynamicBatchScheduler(settings.batch_policy(), self._execute_dynamic_batch)
            if settings.dynamic_batching
            else None
        )
        self._batching_mode = "dynamic" if self._batch_scheduler is not None else "legacy"
        self._batching_error: str | None = None

    def load(self) -> None:
        try:
            self.backend.load()
            self.ready = True
            self.load_error = None
        except Exception as exc:
            self.ready = False
            self.load_error = f"{type(exc).__name__}: {exc}"[:2000]

    async def ensure_loaded(self) -> None:
        if self.ready or self.load_error is not None:
            return
        async with self._load_lock:
            if not self.ready and self.load_error is None:
                await asyncio.to_thread(self.load)

    async def start(self) -> None:
        if self._batch_scheduler is None:
            return
        try:
            self._batch_scheduler.start()
            self._batching_mode = "dynamic"
            self._batching_error = None
        except Exception as exc:
            self._batch_scheduler = None
            self._batching_mode = "legacy"
            self._batching_error = f"{type(exc).__name__}: {exc}"[:1000]

    async def shutdown(self) -> None:
        if self._batch_scheduler is not None:
            await self._batch_scheduler.close()

    async def _execute_dynamic_batch(
        self,
        texts: list[str],
        input_types: list[str],
    ) -> list[np.ndarray]:
        return await asyncio.to_thread(
            self.backend.embed,
            texts,
            input_types,
            self.settings.model_batch_size,
        )

    def health(self) -> dict[str, Any]:
        runtime = self.backend.runtime_details()
        return {
            "status": "ready" if self.ready else "not_ready",
            "module": "P3-B1",
            "backend": self.backend.backend_key,
            "requested_backend": runtime.get("requested_backend", self.settings.backend_name),
            "engine": self.backend.engine_name,
            "model": self.backend.model_name,
            "model_hash": runtime.get("model_hash"),
            "dimension": self.backend.dimension,
            "provider": runtime.get("provider"),
            "device": runtime.get("device", "CPU"),
            "precision": runtime.get("actual_precision", runtime.get("precision")),
            "requested_precision": self.settings.precision.upper(),
            "fallback_used": runtime.get("fallback_used", False),
            "fallback_history": runtime.get("fallback_history", []),
            "threads": self.settings.threads,
            "model_batch_size": self.settings.model_batch_size,
            "max_batch_items": self.settings.max_batch_items,
            "batching_mode": self._batching_mode,
            "batching_error": self._batching_error,
            "dynamic_batch_enabled": self._batch_scheduler is not None,
            "dynamic_max_batch_items": self.settings.dynamic_max_batch_items,
            "dynamic_max_batch_tokens": self.settings.dynamic_max_batch_tokens,
            "dynamic_max_wait_ms": self.settings.dynamic_max_wait_ms,
            "dynamic_queue_size": self.settings.dynamic_queue_size,
            "inference_mode": runtime.get("inference_mode", "sync"),
            "load_error": self.load_error,
        }

    def capabilities(self) -> dict[str, Any]:
        runtime = self.backend.runtime_details()
        return {
            "module": "P3-B1",
            "current_backend": runtime,
            "batching": {
                "mode": self._batching_mode,
                "model_batch_size": self.settings.model_batch_size,
                "max_batch_items": self.settings.max_batch_items,
                "model_batch_size_env": "AETHER_B1_MODEL_BATCH_SIZE",
                "dynamic_enabled": self._batch_scheduler is not None,
                "dynamic_queue_size": self.settings.dynamic_queue_size,
                "dynamic_max_batch_items": self.settings.dynamic_max_batch_items,
                "dynamic_max_batch_tokens": self.settings.dynamic_max_batch_tokens,
                "dynamic_max_wait_ms": self.settings.dynamic_max_wait_ms,
                "dynamic_length_buckets": list(self.settings.dynamic_length_buckets),
            },
            "backend_strategy": describe_backends(
                self.settings.backend_name,
                self.backend.backend_key if self.ready else None,
            ),
            "cpu_runtime": detect_runtime_capabilities(self.settings.enable_simd),
            "profiling": {
                "enabled": self.settings.profile_timing,
                "stages": [
                    "validation",
                    "chunking",
                    "queue_wait",
                    "model_inference",
                    "vector_postprocess",
                    "response_postprocess",
                    "server_total",
                    "unaccounted",
                ],
                "network": "measured by the HTTP benchmark as client_total - server_total",
            },
            "simd_kernel_strategy": {
                "current": (
                    "numpy-vectorized-l2-and-inference-runtime-dispatch"
                    if self.settings.enable_simd
                    else "portable-runtime-baseline"
                ),
                "implemented": [
                    "numpy-vectorized-l2",
                    "runtime-auto-dispatch-with-avx2-acceptance",
                ],
                "research_implemented": ["scalar", "explicit-avx2"],
                "reserved": ["explicit-avx512", "amx-bf16", "amx-int8"],
                "selection_contract": (
                    "runtime capability check plus scalar-compatible fallback; framework ISA "
                    "selection is runtime-managed"
                ),
            },
        }

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, self.settings.event_history_size))
        return list(self._events)[-bounded:][::-1]

    def metrics_snapshot(self) -> dict[str, Any]:
        snapshot = {
            **self.metrics.snapshot(),
            "backend": self.backend.backend_key,
            "batching_mode": self._batching_mode,
        }
        if self._batch_scheduler is not None:
            snapshot["dynamic_batch"] = self._batch_scheduler.snapshot()
        else:
            snapshot["dynamic_batch"] = {
                "dynamic_batch_enabled": False,
                "queue_depth": 0,
                "queue_capacity": self.settings.dynamic_queue_size,
            }
        return snapshot

    def _cache_result(self, item: InterceptItem, digest: str, result: dict[str, Any]) -> None:
        key = (item.tenant_id, item.request_id)
        self._cache[key] = (digest, copy.deepcopy(result))
        self._cache.move_to_end(key)
        while len(self._cache) > self.settings.idempotency_cache_size:
            self._cache.popitem(last=False)

    def _start_inflight(
        self,
        item: InterceptItem,
        digest: str,
    ) -> tuple[asyncio.Future[dict[str, Any]], bool]:
        key = (item.tenant_id, item.request_id)
        existing = self._inflight.get(key)
        if existing is not None:
            existing_digest, future = existing
            if existing_digest != digest:
                raise ValueError("idempotency conflict")
            return future, False
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = (digest, future)
        return future, True

    def _finish_inflight(
        self,
        item: InterceptItem,
        digest: str,
        result: dict[str, Any],
    ) -> None:
        key = (item.tenant_id, item.request_id)
        existing = self._inflight.get(key)
        if existing is None or existing[0] != digest:
            return
        future = existing[1]
        if not future.done():
            future.set_result(copy.deepcopy(result))
        self._inflight.pop(key, None)

    async def _collect_inflight(
        self,
        results: list[dict[str, Any] | None],
        waiters: dict[int, tuple[InterceptItem, asyncio.Future[dict[str, Any]]]],
    ) -> None:
        for index, (item, future) in waiters.items():
            try:
                primary = await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=self.settings.backend_timeout_seconds
                    + self.settings.queue_timeout_seconds,
                )
            except TimeoutError:
                results[index] = _error_result(
                    item.model_dump(),
                    "B1_BUSY",
                    "timed out waiting for the in-flight idempotent request",
                    item.trace_id,
                )
            else:
                replay = copy.deepcopy(primary)
                replay["idempotent_replay"] = True
                results[index] = replay

    def _record_events(self, results: list[dict[str, Any]]) -> None:
        for result in results:
            self._event_sequence += 1
            status = str(result.get("status"))
            if status == "success":
                stages = [
                    "intercepted",
                    "validated",
                    "chunked",
                    "embedded",
                    "normalized",
                    "returned",
                ]
                failed_at = None
            elif status == "skipped":
                stages = ["intercepted", "validated", "skipped", "returned"]
                failed_at = None
            else:
                failed_at = _failure_stage(result.get("error_code"))
                stages = ["intercepted", "failed"]
            self._events.append(
                {
                    "sequence": self._event_sequence,
                    "timestamp_epoch": time.time(),
                    "request_id": result.get("request_id"),
                    "trace_id": result.get("trace_id"),
                    "tenant_id": result.get("tenant_id"),
                    "source_type": result.get("source_type"),
                    "source_id": result.get("source_id"),
                    "status": status,
                    "error_code": result.get("error_code"),
                    "failed_at": failed_at,
                    "input_chars": result.get("input_chars", 0),
                    "chunk_count": result.get("chunk_count", 0),
                    "vector_count": result.get("vector_count", 0),
                    "embedding_dim": result.get("embedding_dim"),
                    "latency_ms": result.get("latency_ms", 0.0),
                    "embedding_latency_ms": result.get("embedding_latency_ms", 0.0),
                    "idempotent_replay": result.get("idempotent_replay", False),
                    "backend": result.get("backend", self.backend.backend_key),
                    "precision": result.get("quantization_type"),
                    "fallback_used": result.get("fallback_used", False),
                    "stages": stages,
                }
            )

    @staticmethod
    def _embedding_inputs(
        valid: list[tuple[int, InterceptItem, str, list[tuple[int, int, str]]]],
    ) -> tuple[list[str], list[str], list[tuple[int, int, int, int]]]:
        texts: list[str] = []
        input_types: list[str] = []
        owners: list[tuple[int, int, int, int]] = []
        for index, item, _, chunks in valid:
            for chunk_index, (start, end, text) in enumerate(chunks):
                texts.append(text)
                input_types.append(item.input_type)
                owners.append((index, chunk_index, start, end))
        return texts, input_types, owners

    async def _embed_dynamic(
        self,
        valid: list[tuple[int, InterceptItem, str, list[tuple[int, int, str]]]],
        texts: list[str],
        input_types: list[str],
        owners: list[tuple[int, int, int, int]],
    ) -> list[EmbeddingJobResult]:
        if self._batch_scheduler is None:
            raise DynamicBatchClosed("dynamic batch scheduler is not configured")
        item_by_index = {index: item for index, item, _, _ in valid}
        futures: list[asyncio.Future[EmbeddingJobResult]] = []
        try:
            for text, input_type, owner in zip(texts, input_types, owners, strict=True):
                item_index, _, _, _ = owner
                item = item_by_index[item_index]
                futures.append(
                    self._batch_scheduler.submit(
                        text=text,
                        input_type=input_type,  # type: ignore[arg-type]
                        request_id=item.request_id,
                        trace_id=item.trace_id,
                        estimated_tokens=estimate_tokens(text, max_length=self.settings.max_length),
                        timeout_seconds=self.settings.queue_timeout_seconds,
                    )
                )
        except Exception:
            for future in futures:
                future.cancel()
            raise
        timeout = (
            self.settings.queue_timeout_seconds
            + self.settings.backend_timeout_seconds
            + self.settings.dynamic_max_wait_ms / 1000.0
            + 1.0
        )
        try:
            gathered = await asyncio.wait_for(
                asyncio.gather(*futures, return_exceptions=True),
                timeout=timeout,
            )
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        first_error: BaseException | None = None
        results: list[EmbeddingJobResult] = []
        for value in gathered:
            if isinstance(value, BaseException):
                first_error = value
                break
            results.append(value)
        if first_error is not None:
            for future in futures:
                future.cancel()
            raise first_error
        return results

    @staticmethod
    def _error_from_embedding_exception(exc: BaseException) -> tuple[str, str]:
        if isinstance(exc, (DynamicBatchQueueFull, DynamicBatchClosed, DynamicBatchTimeout)):
            return "B1_BUSY", str(exc) or "dynamic batch queue is unavailable"
        if isinstance(exc, TimeoutError):
            return "B1_EMBEDDING_TIMEOUT", str(exc) or "embedding timed out"
        return "B1_EMBEDDING_BACKEND_ERROR", f"{type(exc).__name__}: {exc}"

    def _build_vector_map(
        self,
        owners: list[tuple[int, int, int, int]],
        vectors: list[np.ndarray],
    ) -> dict[int, list[tuple[int, int, int, np.ndarray]]]:
        vector_map: dict[int, list[tuple[int, int, int, np.ndarray]]] = {}
        for owner, vector in zip(owners, vectors, strict=True):
            item_index, chunk_index, start, end = owner
            vector_map.setdefault(item_index, []).append((chunk_index, start, end, vector))
        return vector_map

    def _write_success_results(
        self,
        *,
        valid: list[tuple[int, InterceptItem, str, list[tuple[int, int, str]]]],
        results: list[dict[str, Any] | None],
        vector_map: dict[int, list[tuple[int, int, int, np.ndarray]]],
        runtime: dict[str, Any],
        precision: str,
        actual_backend: str,
        embedding_latency_ms: float,
        embed_started: float,
    ) -> None:
        for index, item, digest, _ in valid:
            chunk_results = []
            values = vector_map[index]
            for chunk_index, start, end, vector in values:
                base_chunk_id = item.chunk_id or item.source_id
                chunk_id = (
                    base_chunk_id if len(values) == 1 else f"{base_chunk_id}:{chunk_index:04d}"
                )
                chunk_results.append(
                    {
                        "chunk_id": chunk_id,
                        "chunk_index": chunk_index,
                        "start_char": start,
                        "end_char": end,
                        "chunk_text": item.content[start:end],
                        "vector": vector.tolist(),
                        "metadata": {
                            **item.metadata,
                            "tenant_id": item.tenant_id,
                            "source_type": item.source_type,
                            "source_id": item.source_id,
                            "object_id": item.object_id,
                            "chunk_index": chunk_index,
                            "start_char": start,
                            "end_char": end,
                            "b1_schema_version": "1.1",
                            "embedding_backend": actual_backend,
                            "embedding_model": self.backend.model_name,
                            "embedding_dim": self.backend.dimension,
                            "embedding_precision": precision,
                            "model_hash": runtime.get("model_hash"),
                        },
                    }
                )
            result = {
                "request_id": item.request_id,
                "trace_id": item.trace_id,
                "tenant_id": item.tenant_id,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "object_id": item.object_id,
                "status": "success",
                "error_code": None,
                "error_message": None,
                "backend": actual_backend,
                "requested_backend": runtime.get("requested_backend", self.settings.backend_name),
                "engine": self.backend.engine_name,
                "embedding_model": self.backend.model_name,
                "input_type": item.input_type,
                "model_hash": runtime.get("model_hash"),
                "embedding_dim": self.backend.dimension,
                "device": runtime.get("device", "CPU"),
                "normalized": True,
                "quantization_type": precision,
                "quantization_mode": runtime.get("quantization_mode", "none"),
                "fallback_used": runtime.get("fallback_used", False),
                "fallback_history": runtime.get("fallback_history", []),
                "schema_version": "1.1",
                "latency_ms": round((time.perf_counter() - embed_started) * 1000, 3),
                "embedding_latency_ms": round(embedding_latency_ms, 3),
                "input_chars": len(item.content),
                "chunk_count": len(chunk_results),
                "vector_count": len(chunk_results),
                "chunks": chunk_results,
            }
            if len(chunk_results) == 1:
                result["chunk_id"] = chunk_results[0]["chunk_id"]
                result["vector"] = chunk_results[0]["vector"]
            results[index] = result
            self._cache_result(item, digest, result)
            self._finish_inflight(item, digest, result)

    async def process(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        if "items" in body and len(body) != 1:
            raise HTTPException(
                status_code=422,
                detail="batch requests must contain only the top-level items field",
            )
        raw_items = body.get("items") if "items" in body else [body]
        if not isinstance(raw_items, list):
            raise HTTPException(status_code=422, detail="items must be an array")
        if not raw_items:
            raise HTTPException(status_code=422, detail="items must not be empty")
        if len(raw_items) > self.settings.max_batch_items:
            raise HTTPException(status_code=413, detail="batch item limit exceeded")

        self.metrics.begin(len(raw_items))
        results: list[dict[str, Any] | None] = [None] * len(raw_items)
        valid: list[tuple[int, InterceptItem, str, list[tuple[int, int, str]]]] = []
        waiters: dict[int, tuple[InterceptItem, asyncio.Future[dict[str, Any]]]] = {}
        profile_timing = self.settings.profile_timing
        timings = {
            "validation_ms": 0.0,
            "chunking_ms": 0.0,
            "queue_wait_ms": 0.0,
            "model_inference_ms": 0.0,
            "vector_postprocess_ms": 0.0,
            "response_postprocess_ms": 0.0,
        }
        preprocess_started = time.perf_counter()

        for index, raw in enumerate(raw_items):
            item_started = time.perf_counter()
            try:
                item = InterceptItem.model_validate(raw)
            except ValidationError as exc:
                results[index] = _error_result(raw, "B1_INVALID_REQUEST", str(exc))
                continue
            trace_id = item.trace_id or uuid.uuid4().hex
            if not item.embedding_required:
                results[index] = {
                    "request_id": item.request_id,
                    "trace_id": trace_id,
                    "tenant_id": item.tenant_id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "status": "skipped",
                    "error_code": None,
                    "error_message": None,
                    "reason": "embedding_required=false",
                    "latency_ms": round((time.perf_counter() - item_started) * 1000, 3),
                    "embedding_latency_ms": 0.0,
                    "input_chars": len(item.content),
                    "chunk_count": 0,
                    "vector_count": 0,
                    "chunks": [],
                }
                continue
            if not item.content.strip():
                results[index] = _error_result(
                    raw, "B1_EMPTY_TEXT", "text must not be blank", trace_id
                )
                continue
            if len(item.content) > self.settings.max_input_chars:
                results[index] = _error_result(
                    raw,
                    "B1_TEXT_TOO_LONG",
                    f"text exceeds {self.settings.max_input_chars} characters",
                    trace_id,
                )
                continue
            metadata_size = len(json.dumps(item.metadata, ensure_ascii=False).encode("utf-8"))
            if metadata_size > self.settings.max_metadata_bytes:
                results[index] = _error_result(
                    raw,
                    "B1_METADATA_TOO_LARGE",
                    f"metadata exceeds {self.settings.max_metadata_bytes} bytes",
                    trace_id,
                )
                continue

            digest = _digest(item)
            cache_key = (item.tenant_id, item.request_id)
            cached = self._cache.get(cache_key)
            if cached is not None:
                cached_digest, cached_result = cached
                if cached_digest != digest:
                    results[index] = _error_result(
                        raw,
                        "B1_IDEMPOTENCY_CONFLICT",
                        "request_id was already used with a different payload",
                        trace_id,
                    )
                else:
                    replay = copy.deepcopy(cached_result)
                    replay["idempotent_replay"] = True
                    results[index] = replay
                continue
            chunk_started = time.perf_counter()
            if item.preserve_input:
                if not item.content.strip() or len(item.content) > self.settings.chunk_max_chars:
                    results[index] = _error_result(
                        raw,
                        "B1_FIXED_INPUT_INVALID",
                        "fixed input is empty or exceeds the configured single-input limit",
                        trace_id,
                    )
                    continue
                chunks = [(0, len(item.content), item.content)]
            else:
                chunks = _natural_chunks(
                    item.content,
                    self.settings.chunk_max_chars,
                    self.settings.chunk_overlap_chars,
                )
            if profile_timing:
                timings["chunking_ms"] += (time.perf_counter() - chunk_started) * 1000
            if len(chunks) > self.settings.max_chunks_per_item:
                results[index] = _error_result(
                    raw,
                    "B1_TOO_MANY_CHUNKS",
                    f"chunk count exceeds {self.settings.max_chunks_per_item}",
                    trace_id,
                )
                continue
            item.trace_id = trace_id
            try:
                future, is_owner = self._start_inflight(item, digest)
            except ValueError:
                results[index] = _error_result(
                    raw,
                    "B1_IDEMPOTENCY_CONFLICT",
                    "request_id is already in flight with a different payload",
                    trace_id,
                )
                continue
            if not is_owner:
                waiters[index] = (item, future)
                continue
            valid.append((index, item, digest, chunks))

        if profile_timing:
            timings["validation_ms"] = max(
                0.0,
                (time.perf_counter() - preprocess_started) * 1000 - timings["chunking_ms"],
            )

        if valid and not self.ready and self.load_error is None:
            await self.ensure_loaded()
        if valid and not self.ready:
            for index, item, digest, _ in valid:
                result = _error_result(
                    item.model_dump(),
                    "B1_MODEL_NOT_READY",
                    self.load_error or "embedding model is not ready",
                    item.trace_id,
                )
                results[index] = result
                self._finish_inflight(item, digest, result)
            await self._collect_inflight(results, waiters)
            status_code = 503 if self.settings.fail_mode == "closed" else 200
            return self._finish(results, started, timings), status_code

        acquired = False
        embed_task: asyncio.Task[list[np.ndarray]] | None = None
        if valid:
            dynamic_path = False
            try:
                texts, input_types, owners = self._embedding_inputs(valid)
                embed_started = time.perf_counter()
                if self._batch_scheduler is not None and self._batch_scheduler.running:
                    dynamic_path = True
                    dynamic_results = await self._embed_dynamic(valid, texts, input_types, owners)
                    vectors = [result.vector for result in dynamic_results]
                    if profile_timing and dynamic_results:
                        timings["queue_wait_ms"] += max(
                            result.queue_wait_ms for result in dynamic_results
                        )
                        timings["model_inference_ms"] += max(
                            result.backend_inference_ms for result in dynamic_results
                        )
                else:
                    queue_started = time.perf_counter()
                    await asyncio.wait_for(
                        self._semaphore.acquire(), timeout=self.settings.queue_timeout_seconds
                    )
                    acquired = True
                    if profile_timing:
                        timings["queue_wait_ms"] += (time.perf_counter() - queue_started) * 1000

                    embed_task = asyncio.create_task(
                        asyncio.to_thread(
                            self.backend.embed,
                            texts,
                            input_types,
                            self.settings.model_batch_size,
                        )
                    )
                    vectors = await asyncio.wait_for(
                        asyncio.shield(embed_task),
                        timeout=self.settings.backend_timeout_seconds,
                    )
                    if profile_timing:
                        timings["model_inference_ms"] += (
                            time.perf_counter() - embed_started
                        ) * 1000
                vector_postprocess_started = time.perf_counter()
                vectors = self._validate_vectors(vectors, len(texts))
                runtime = self.backend.runtime_details()
                precision = str(
                    runtime.get("actual_precision", runtime.get("precision", "FP32"))
                ).upper()
                actual_backend = str(runtime.get("actual_backend", self.backend.backend_key))
                vector_map = self._build_vector_map(owners, vectors)
                if profile_timing:
                    timings["vector_postprocess_ms"] += (
                        time.perf_counter() - vector_postprocess_started
                    ) * 1000
                embedding_latency_ms = (time.perf_counter() - embed_started) * 1000

                response_postprocess_started = time.perf_counter()
                self._write_success_results(
                    valid=valid,
                    results=results,
                    vector_map=vector_map,
                    runtime=runtime,
                    precision=precision,
                    actual_backend=actual_backend,
                    embedding_latency_ms=embedding_latency_ms,
                    embed_started=embed_started,
                )
                if profile_timing:
                    timings["response_postprocess_ms"] += (
                        time.perf_counter() - response_postprocess_started
                    ) * 1000
            except (DynamicBatchQueueFull, DynamicBatchClosed, DynamicBatchTimeout) as exc:
                self.metrics.queue_timeouts += 1
                error_code, error_message = self._error_from_embedding_exception(exc)
                for index, item, digest, _ in valid:
                    if results[index] is None:
                        result = _error_result(
                            item.model_dump(),
                            error_code,
                            error_message,
                            item.trace_id,
                        )
                        results[index] = result
                        self._finish_inflight(item, digest, result)
                await self._collect_inflight(results, waiters)
                status_code = 503 if self.settings.fail_mode == "closed" else 200
                return self._finish(results, started, timings), status_code
            except TimeoutError as exc:
                if embed_task is not None and not embed_task.done():
                    self.metrics.backend_timeouts += 1
                    embed_task.add_done_callback(lambda _: self._semaphore.release())
                    acquired = False
                    error_code = "B1_EMBEDDING_TIMEOUT"
                    error_message = (
                        f"embedding exceeded {self.settings.backend_timeout_seconds} seconds"
                    )
                elif embed_task is None and not dynamic_path:
                    self.metrics.queue_timeouts += 1
                    error_code = "B1_BUSY"
                    error_message = "embedding queue wait timed out"
                else:
                    self.metrics.backend_timeouts += 1
                    error_code = "B1_EMBEDDING_TIMEOUT"
                    error_message = f"TimeoutError: {exc}"
                for index, item, digest, _ in valid:
                    if results[index] is None:
                        result = _error_result(
                            item.model_dump(),
                            error_code,
                            error_message,
                            item.trace_id,
                        )
                        results[index] = result
                        self._finish_inflight(item, digest, result)
                await self._collect_inflight(results, waiters)
                status_code = 503 if self.settings.fail_mode == "closed" else 200
                return self._finish(results, started, timings), status_code
            except asyncio.CancelledError:
                if embed_task is not None and not embed_task.done():
                    embed_task.add_done_callback(lambda _: self._semaphore.release())
                    acquired = False
                for _, item, digest, _ in valid:
                    cancelled = _error_result(
                        item.model_dump(),
                        "B1_REQUEST_CANCELLED",
                        "request processing was cancelled",
                        item.trace_id,
                    )
                    self._finish_inflight(item, digest, cancelled)
                raise
            except Exception as exc:
                self.metrics.backend_failures += 1
                if isinstance(exc, BackendUnavailableError):
                    self.ready = False
                    self.load_error = f"{type(exc).__name__}: {exc}"[:2000]
                for index, item, digest, _ in valid:
                    if results[index] is None:
                        result = _error_result(
                            item.model_dump(),
                            "B1_EMBEDDING_BACKEND_ERROR",
                            f"{type(exc).__name__}: {exc}",
                            item.trace_id,
                        )
                        results[index] = result
                        self._finish_inflight(item, digest, result)
                await self._collect_inflight(results, waiters)
                status_code = 503 if self.settings.fail_mode == "closed" else 200
                return self._finish(results, started, timings), status_code
            finally:
                if acquired:
                    self._semaphore.release()

        await self._collect_inflight(results, waiters)
        return self._finish(results, started, timings), 200

    def _finish(
        self,
        results: list[dict[str, Any] | None],
        started: float,
        timings: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        latency_ms = (time.perf_counter() - started) * 1000
        finalized = [result for result in results if result is not None]
        if not finalized:
            raise RuntimeError("B1 produced no item results")
        self.metrics.finish(finalized, latency_ms)
        self._record_events(finalized)
        statuses = {str(result["status"]) for result in finalized}
        overall = (
            "success"
            if statuses == {"success"}
            else "partial"
            if len(statuses) > 1
            else next(iter(statuses))
        )
        payload = {
            "module": "P3-B1",
            "overall_status": overall,
            "latency_ms": round(latency_ms, 3),
            "results": finalized,
        }
        if self.settings.profile_timing:
            stage_timings = dict(timings or {})
            stage_timings["server_total_ms"] = latency_ms
            accounted = sum(
                value for key, value in stage_timings.items() if key != "server_total_ms"
            )
            stage_timings["unaccounted_ms"] = max(0.0, latency_ms - accounted)
            payload["profiling"] = {key: round(value, 3) for key, value in stage_timings.items()}
        return payload

    def _validate_vectors(self, vectors: list[np.ndarray], expected: int) -> list[np.ndarray]:
        if len(vectors) != expected:
            raise ValueError("embedding result count mismatch")
        prepared: list[np.ndarray] = []
        dimension: int | None = None
        for value in vectors:
            vector = np.asarray(value, dtype=np.float32).reshape(-1)
            if vector.size == 0 or not np.isfinite(vector).all():
                raise ValueError("embedding vector is empty or non-finite")
            if dimension is None:
                dimension = int(vector.size)
            elif vector.size != dimension:
                raise ValueError("embedding dimensions are inconsistent")
            prepared.append(vector)
        matrix = np.stack(prepared, axis=0)
        norms = np.linalg.norm(matrix, axis=1)
        if not np.isfinite(norms).all() or np.any(norms <= 0):
            raise ValueError("embedding norm is invalid")
        # NumPy dispatches this contiguous batch operation to its validated SIMD runtime.
        normalized_matrix = np.asarray(matrix / norms[:, np.newaxis], dtype=np.float32)
        normalized = [np.ascontiguousarray(row) for row in normalized_matrix]
        if self.backend.dimension is None:
            self.backend.dimension = dimension
        elif dimension != self.backend.dimension:
            raise ValueError(
                f"embedding dimension changed from {self.backend.dimension} to {dimension}"
            )
        return normalized


def create_app(
    settings: SidecarSettings | None = None,
    backend: SidecarEmbeddingBackend | None = None,
) -> FastAPI:
    settings = settings or SidecarSettings()
    if backend is not None:
        selected_backend = backend
    else:
        try:
            selected_backend = create_backend_chain(
                settings.backend_name,
                settings.backend_config(),
                settings.fallback_backends,
                settings.allow_backend_fallback,
            )
        except BackendUnavailableError as exc:
            selected_backend = _UnavailableBackend(
                settings.backend_name,
                settings.model_name,
                str(exc),
            )
    service = B1Service(settings, selected_backend)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.eager_load:
            await service.ensure_loaded()
        await service.start()
        try:
            yield
        finally:
            await service.shutdown()

    app = FastAPI(
        title="Aether P3-B1 CPU Embedding Sidecar",
        version="1.1.0",
        lifespan=lifespan,
    )
    app.state.service = service

    @app.middleware("http")
    async def enforce_body_limit(request: Request, call_next: Any) -> Any:
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    parsed_length = int(content_length)
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "invalid content-length header"},
                    )
                if parsed_length < 0:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "invalid content-length header"},
                    )
                if parsed_length > settings.max_body_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "request body too large"},
                    )
            body = await request.body()
            if len(body) > settings.max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "request body too large"},
                )
        return await call_next(request)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "alive", "module": "P3-B1"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        return JSONResponse(service.health(), status_code=200 if service.ready else 503)

    @app.get("/metrics")
    async def metrics() -> dict[str, Any]:
        return service.metrics_snapshot()

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return service.capabilities()

    @app.get("/v1/events")
    async def events(limit: int = 20) -> dict[str, Any]:
        return {"items": service.recent_events(limit), "limit": limit}

    @app.post("/v1/intercept")
    @app.post("/v1/embeddings")
    async def intercept(body: dict[str, Any]) -> JSONResponse:
        payload, status_code = await service.process(body)
        return JSONResponse(payload, status_code=status_code)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = SidecarSettings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
    )


if __name__ == "__main__":
    main()
