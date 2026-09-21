"""Bound local CPU inference for SemanticEmbeddingCapability, without legacy imports.

The first native profile is BGE small Chinese v1.5 (CLS + L2 + float32). Other
model families need an explicit preprocessing/pooling profile before registration.
Production callers supply an approved binding; inspection alone does not approve
a model for an existing retrieval space.
"""

from __future__ import annotations

import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

import numpy as np
from pydantic import Field, model_validator

from aether_agent_memory.recall.embedding.backends import (
    BackendConfig,
    BackendUnavailableError,
    FastEmbedOnnxBackend,
    IpexBackend,
    OpenVinoBackend,
    create_backend,
)
from aether_agent_memory.recall.embedding.models import (
    EmbeddingModelBinding,
    SemanticEmbeddingRequest,
)
from aether_agent_memory.recall.embedding.service import (
    ComputedEmbedding,
    EmbeddingRuntime,
    SemanticEmbeddingError,
)
from aether_agent_memory.runtime.capability_store import AtomicRecordStore
from aether_agent_memory.runtime.contract_types import (
    ContractModel,
    EmbeddingUsage,
    PositiveInt,
    hash_bytes,
    hash_json,
    hash_text,
    utcnow,
    vector_bytes,
)

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
SCHEMA_VERSION = "bge-cls-l2-float32-v1"


class NativeEmbeddingSettings(ContractModel):
    backend: Literal["onnx", "openvino", "ipex"] = "onnx"
    model_name: Literal["BAAI/bge-small-zh-v1.5"] = "BAAI/bge-small-zh-v1.5"
    model_path: Path | None = None
    cache_dir: Path = Path(".aether/recall/embedding/models")
    precision: Literal["fp32", "int8"] = "fp32"
    threads: PositiveInt = 1
    max_input_tokens: int = Field(default=512, strict=True, ge=1, le=512)
    query_prefix: str = "为这个句子生成表示以用于检索相关文章："
    passage_prefix: str = ""
    openvino_async: bool = False
    openvino_infer_requests: int = Field(default=0, strict=True, ge=0)

    @model_validator(mode="after")
    def supported_precision(self) -> NativeEmbeddingSettings:
        if self.backend == "ipex" and self.precision != "fp32":
            raise ValueError("the native IPEX profile only supports fp32")
        return self


def _runtime_versions(backend: str) -> dict[str, str]:
    packages = {
        "onnx": ("fastembed", "onnxruntime", "tokenizers"),
        "openvino": ("openvino", "optimum-intel", "transformers", "tokenizers"),
        "ipex": ("torch", "intel-extension-for-pytorch", "transformers", "tokenizers"),
    }
    result = {}
    for package in packages[backend]:
        try:
            result[package] = version(package)
        except PackageNotFoundError as exc:
            raise BackendUnavailableError(
                f"required inference package is missing: {package}"
            ) from exc
    return result


class NativeEmbeddingBackend:
    """One loaded model instance with one bounded native worker and no retries.

    Cancellation cannot stop a running CPU kernel. The worker stays occupied
    until the underlying future actually completes; another request gets BUSY.
    Query and Passage should use separate instances and separate service quotas.
    """

    def __init__(self, settings: NativeEmbeddingSettings) -> None:
        self.settings = settings
        config = BackendConfig(
            model_name=settings.model_name,
            cache_dir=settings.cache_dir,
            model_path=settings.model_path,
            threads=settings.threads,
            precision=settings.precision,
            max_length=settings.max_input_tokens,
            model_batch_size=1,
            openvino_async=settings.openvino_async,
            openvino_infer_requests=settings.openvino_infer_requests,
        )
        # Deliberately use a single backend, never create_backend_chain.
        backend = create_backend(settings.backend, config)
        if not isinstance(backend, (FastEmbedOnnxBackend, OpenVinoBackend, IpexBackend)):
            raise BackendUnavailableError("native profile requires a built-in CPU backend")
        backend.load()
        self._backend = backend
        if (
            backend.backend_key != settings.backend
            or backend.precision.lower() != settings.precision
        ):
            raise BackendUnavailableError(
                "loaded engine/precision does not match configured backend"
            )
        if backend.model_name != MODEL_NAME or backend.dimension != 512:
            raise BackendUnavailableError(
                "loaded model does not match the BGE 512-dimension profile"
            )
        details = backend.runtime_details()
        model_hash = details.get("model_hash")
        if not isinstance(model_hash, str) or re.fullmatch(r"[0-9a-f]{64}", model_hash) is None:
            raise BackendUnavailableError("backend must provide a SHA-256 digest of actual weights")
        if isinstance(backend, FastEmbedOnnxBackend):
            from fastembed.text.onnx_embedding import OnnxTextEmbedding

            model = backend._model.model
            if type(model) is not OnnxTextEmbedding:
                raise BackendUnavailableError("unsupported FastEmbed preprocessing implementation")
            tokenizer = model.tokenizer
        else:
            tokenizer = getattr(backend._tokenizer, "backend_tokenizer", None)
        if tokenizer is None:
            raise BackendUnavailableError("loaded model must expose its actual fast tokenizer")
        from tokenizers import Tokenizer

        # Clone the loaded tokenizer: counting must include prefixes/special tokens
        # without changing the engine's shared padding or truncation configuration.
        self._counter: Any = Tokenizer.from_str(tokenizer.to_str())
        self._counter.no_truncation()
        self._counter.no_padding()
        self.max_input_tokens = settings.max_input_tokens
        truncation = getattr(tokenizer, "truncation", None)
        if truncation is not None:
            self.max_input_tokens = min(self.max_input_tokens, int(truncation["max_length"]))
        preprocessing = {
            "schema": SCHEMA_VERSION,
            "backend": settings.backend,
            "precision": settings.precision,
            "runtime_versions": _runtime_versions(settings.backend),
            "tokenizer_sha256": hash_text(tokenizer.to_str()).value,
            "query_prefix": settings.query_prefix,
            "passage_prefix": settings.passage_prefix,
            "max_input_tokens": self.max_input_tokens,
        }
        self.model_version = model_hash
        self.preprocessing_version = hash_json(preprocessing).value
        self._details = {**details, "preprocessing": preprocessing}
        self._binding: EmbeddingModelBinding | None = None
        self._records: AtomicRecordStore | None = None
        self._busy = Lock()
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recall-embedding")

    def describe(self) -> dict[str, Any]:
        """Observed deployment evidence; does not approve a retrieval space."""
        return {
            "model_id": MODEL_NAME,
            "model_version": self.model_version,
            "dimension": 512,
            "dtype": "float32",
            "embedding_schema_version": SCHEMA_VERSION,
            "preprocessing_version": self.preprocessing_version,
            "max_input_tokens": self.max_input_tokens,
            "runtime": self._details,
        }

    def bind(
        self, binding: EmbeddingModelBinding, *, records: AtomicRecordStore
    ) -> EmbeddingRuntime:
        if self._closed:
            raise BackendUnavailableError("native backend is closed")
        observed = self.describe()
        if any(
            getattr(binding, field) != observed[field]
            for field in (
                "model_id",
                "model_version",
                "dimension",
                "dtype",
                "embedding_schema_version",
                "preprocessing_version",
            )
        ):
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        if self._binding is not None and self._binding != binding:
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        if self._records is not None and self._records is not records:
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        self._binding = binding
        self._records = records
        return EmbeddingRuntime(
            binding=binding,
            backend=self,
            count_tokens=self.count_tokens,
            max_input_tokens=self.max_input_tokens,
        )

    def _formatted(self, text: str, usage: EmbeddingUsage) -> str:
        if usage not in ("Query", "Passage"):
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")
        prefix = self.settings.query_prefix if usage == "Query" else self.settings.passage_prefix
        return prefix + text

    def count_tokens(self, text: str, usage: EmbeddingUsage) -> int:
        return len(self._counter.encode(self._formatted(text, usage), add_special_tokens=True).ids)

    async def compute(self, request: SemanticEmbeddingRequest, text: str) -> ComputedEmbedding:
        if self._closed:
            raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED")
        if self._binding is None or request.model_binding != self._binding:
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        if hash_text(text) != request.source_hash or not text.strip():
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")
        if self.count_tokens(text, request.usage) > self.max_input_tokens:
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")
        if utcnow() >= request.deadline_at:
            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
        if not self._busy.acquire(blocking=False):
            raise SemanticEmbeddingError("EMBEDDING_BUSY")
        try:
            future = self._pool.submit(self._compute, request, text)
        except BaseException:
            self._busy.release()
            raise
        # This callback belongs to the real concurrent future, not the cancellable
        # asyncio attachment. A timed-out caller cannot free a still-running slot.
        future.add_done_callback(lambda _: self._busy.release())
        return await asyncio.wrap_future(future)

    def _compute(self, request: SemanticEmbeddingRequest, text: str) -> ComputedEmbedding:
        if utcnow() >= request.deadline_at:
            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
        try:
            vectors = self._backend.embed(
                [self._formatted(text, request.usage)],
                [request.usage.lower()],
                batch_size=1,
            )
            if len(vectors) != 1:
                raise ValueError("expected one vector")
            vector = np.asarray(vectors[0], dtype=np.float32)
            if vector.shape != (512,) or not np.isfinite(vector).all():
                raise ValueError("invalid vector shape or non-finite components")
            norm = float(np.linalg.norm(vector.astype(np.float64)))
            if not np.isfinite(norm) or norm <= 0:
                raise ValueError("invalid vector norm")
            vector = (vector.astype(np.float64) / norm).astype(np.float32)
        except Exception as exc:
            raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED") from exc
        if utcnow() >= request.deadline_at:
            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
        evidence_ref = uuid4().hex
        values = vector.tolist()
        evidence = {
            "evidence_ref": evidence_ref,
            "tenant_id": request.tenant_id,
            "request_ref": request.embedding_request_id,
            "usage": request.usage,
            "source_hash": request.source_hash.model_dump(mode="json"),
            "input_binding_digest": request.input_binding_digest.model_dump(mode="json"),
            "model_binding": request.model_binding.model_dump(mode="json"),
            "deployment": self.describe(),
            "vector_hash": hash_bytes(vector_bytes(values, 512, "float32")).model_dump(mode="json"),
            "computed_at": utcnow().isoformat(),
        }
        assert self._records is not None
        with self._records.transaction() as tx:
            tx.put(
                "semantic-backend-evidence", request.tenant_id, evidence_ref, json.dumps(evidence)
            )
        if utcnow() >= request.deadline_at:
            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
        return ComputedEmbedding(
            vector=values,
            usage=request.usage,
            model_binding=request.model_binding,
            evidence_ref=evidence_ref,
        )

    async def close(self) -> None:
        self._closed = True
        await asyncio.to_thread(self._pool.shutdown, wait=True)

    def shutdown(self) -> None:
        """Synchronous Host shutdown: finish native work before closing its record store."""
        self._closed = True
        self._pool.shutdown(wait=True)
