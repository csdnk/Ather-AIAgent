"""Native binding rules with deterministic engine/tokenizer doubles; no downloads."""

import asyncio
import json
import sys
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

from aether_agent_memory.recall.embedding import native
from aether_agent_memory.recall.embedding.backends import (
    BackendConfig,
    BackendUnavailableError,
    OpenVinoBackend,
)
from aether_agent_memory.recall.embedding.models import EmbeddingModelBinding
from aether_agent_memory.recall.embedding.service import (
    SemanticEmbeddingError,
    make_embedding_request,
)
from aether_agent_memory.runtime.contract_types import hash_json, hash_text, utcnow
from recall_records import InMemoryRecallRecords


class TokenizerDouble:
    truncation = {"max_length": 512}

    def to_str(self):
        return "fixture-tokenizer"

    def no_truncation(self):
        self.truncation = None

    def no_padding(self):
        pass

    def encode(self, text, **kwargs):
        return SimpleNamespace(ids=list(range(len(text) + 2)))


@pytest.fixture
def model(monkeypatch, tmp_path):
    engine = OpenVinoBackend(BackendConfig("BAAI/bge-small-zh-v1.5", tmp_path, None, 1))
    engine.dimension = 512
    engine.model_hash = "a" * 64
    engine._tokenizer = SimpleNamespace(backend_tokenizer=TokenizerDouble())
    calls = []

    def embed(texts, input_types, batch_size):
        calls.append((texts, input_types, batch_size))
        return [np.ones(512, dtype=np.float32)]

    monkeypatch.setattr(engine, "load", lambda: None)
    monkeypatch.setattr(engine, "embed", embed)
    monkeypatch.setattr(native, "create_backend", lambda *args: engine)
    monkeypatch.setattr(native, "_runtime_versions", lambda *args: {"test": "1"})
    monkeypatch.setitem(
        sys.modules,
        "tokenizers",
        SimpleNamespace(
            Tokenizer=SimpleNamespace(from_str=lambda _: TokenizerDouble()),
        ),
    )
    return engine, calls


def binding_for(backend):
    values = backend.describe()
    return EmbeddingModelBinding(
        **{
            key: values[key]
            for key in (
                "model_id",
                "model_version",
                "dimension",
                "dtype",
                "embedding_schema_version",
                "preprocessing_version",
            )
        },
        retrieval_space_ref="fixture-space",
        model_contract_ref="fixture-contract",
    )


def request_for(binding, text="input", usage="Query"):
    return make_embedding_request(
        tenant_id="tenant",
        caller_ref="recall",
        caller_request_ref=usage,
        trace_id="trace",
        authorization_ref="fixture",
        usage=usage,
        input_ref="input",
        source_hash=hash_text(text),
        input_binding_digest=hash_json(text),
        input_binding_ref="approved-input",
        model_binding=binding,
        deadline_at=utcnow() + timedelta(seconds=5),
        execution_policy_ref="embedding-policy-0.1",
    )


async def test_native_formats_usage_normalizes_and_saves_evidence(model):
    engine, calls = model
    backend = native.NativeEmbeddingBackend(
        native.NativeEmbeddingSettings(
            backend="openvino",
        )
    )
    records = InMemoryRecallRecords()
    binding = binding_for(backend)
    backend.bind(binding, records=records)
    try:
        for usage in ("Query", "Passage"):
            request = request_for(binding, text="  原文  ", usage=usage)
            result = await backend.compute(request, "  原文  ")
            assert result.usage == usage and result.model_binding == binding
            assert np.linalg.norm(result.vector) == pytest.approx(1)
            with records.transaction() as tx:
                evidence = json.loads(
                    tx.get("semantic-backend-evidence", "tenant", result.evidence_ref)
                )
            assert evidence["source_hash"] == request.source_hash.model_dump(mode="json")
            assert evidence["usage"] == usage
        assert calls[0][0] == [backend.settings.query_prefix + "  原文  "]
        assert calls[1][0] == ["  原文  "]
        assert engine._tokenizer.backend_tokenizer.truncation is not None
    finally:
        await backend.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("dimension", 3),
        ("model_version", "b" * 64),
        ("preprocessing_version", "bad"),
        ("dtype", "float64"),
    ],
)
async def test_native_rejects_unmatched_deployment_binding(model, field, value):
    backend = native.NativeEmbeddingBackend(
        native.NativeEmbeddingSettings(
            backend="openvino",
        )
    )
    try:
        with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_BINDING_MISMATCH"):
            backend.bind(
                binding_for(backend).replaced(**{field: value}), records=InMemoryRecallRecords()
            )
    finally:
        await backend.close()


async def test_native_checks_hash_prefix_and_special_tokens_before_compute(model):
    backend = native.NativeEmbeddingBackend(
        native.NativeEmbeddingSettings(backend="openvino", max_input_tokens=24)
    )
    binding = binding_for(backend)
    backend.bind(binding, records=InMemoryRecallRecords())
    try:
        # Raw text fits, but Query prefix plus special tokens exceeds the limit.
        text = "x" * 10
        assert backend.count_tokens(text, "Passage") == 12
        assert backend.count_tokens(text, "Query") > 24
        with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_INPUT_INVALID"):
            await backend.compute(request_for(binding, text), text)
        with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_INPUT_INVALID"):
            await backend.compute(request_for(binding, "a", "Passage"), "b")
        assert model[1] == []
    finally:
        await backend.close()


@pytest.mark.parametrize(
    "output",
    [
        [],
        [np.ones(3)],
        [np.zeros(512)],
        [np.full(512, np.nan)],
        [np.full(512, np.inf)],
    ],
)
async def test_native_rejects_invalid_vectors_without_retry(model, monkeypatch, output):
    calls = []

    def embed(*args, **kwargs):
        calls.append(1)
        return output

    monkeypatch.setattr(model[0], "embed", embed)
    backend = native.NativeEmbeddingBackend(
        native.NativeEmbeddingSettings(
            backend="openvino",
        )
    )
    records = InMemoryRecallRecords()
    binding = binding_for(backend)
    backend.bind(binding, records=records)
    try:
        with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_COMPUTE_FAILED"):
            await backend.compute(request_for(binding), "input")
        assert calls == [1]
        with records.transaction() as tx:
            assert not tx.scan("semantic-backend-evidence")
    finally:
        await backend.close()


async def test_cancelled_attachment_keeps_native_slot_until_kernel_finishes(model, monkeypatch):
    started, release = Event(), Event()

    def embed(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return [np.ones(512)]

    monkeypatch.setattr(model[0], "embed", embed)
    backend = native.NativeEmbeddingBackend(
        native.NativeEmbeddingSettings(
            backend="openvino",
        )
    )
    binding = binding_for(backend)
    backend.bind(binding, records=InMemoryRecallRecords())
    task = asyncio.create_task(backend.compute(request_for(binding), "input"))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_BUSY"):
            await backend.compute(request_for(binding), "input")
    finally:
        release.set()
        await backend.close()
    assert not backend._busy.locked()


async def test_deadline_during_inference_does_not_publish_evidence(model, monkeypatch):
    started, release = Event(), Event()

    def embed(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return [np.ones(512)]

    monkeypatch.setattr(model[0], "embed", embed)
    backend = native.NativeEmbeddingBackend(
        native.NativeEmbeddingSettings(
            backend="openvino",
        )
    )
    binding = binding_for(backend)
    records = InMemoryRecallRecords()
    backend.bind(binding, records=records)
    request = request_for(binding)
    task = asyncio.create_task(backend.compute(request, "input"))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        monkeypatch.setattr(native, "utcnow", lambda: request.deadline_at + timedelta(seconds=1))
        release.set()
        with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_DEADLINE_EXCEEDED"):
            await task
        with records.transaction() as tx:
            assert not tx.scan("semantic-backend-evidence")
    finally:
        release.set()
        await backend.close()


@pytest.mark.parametrize("field,value", [("dimension", 3), ("model_hash", "path-name-hash")])
def test_loading_requires_actual_dimension_and_weight_digest(model, field, value):
    setattr(model[0], field, value)
    with pytest.raises(BackendUnavailableError):
        native.NativeEmbeddingBackend(
            native.NativeEmbeddingSettings(
                backend="openvino",
            )
        )
