"""Pinned, evidence-preserving adapter to the existing B1 Sidecar.

Register a deployment contract mapping the model hash/schema to model_binding.
Query and Passage should use separately provisioned endpoints; this adapter
does not claim that client-side semaphores isolate a shared device.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx

from aether_agent_memory.recall.embedding.models import (
    EmbeddingModelBinding,
    SemanticEmbeddingRequest,
)
from aether_agent_memory.recall.embedding.service import ComputedEmbedding, SemanticEmbeddingError
from aether_agent_memory.runtime.capability_store import AtomicRecordStore
from aether_agent_memory.runtime.contract_types import Identifier, identifier, utcnow


class BoundSidecarBackend:
    def __init__(
        self,
        client: httpx.AsyncClient,
        store: AtomicRecordStore,
        binding: EmbeddingModelBinding,
        *,
        expected_model_hash: str,
        response_schema_version: str,
        deployment_contract_ref: str,
        max_input_chars: int,
    ) -> None:
        self.client, self.store, self.binding = client, store, binding
        self.expected_model_hash = identifier(expected_model_hash)
        self.response_schema_version = identifier(response_schema_version)
        self.deployment_contract_ref = identifier(deployment_contract_ref)
        if isinstance(max_input_chars, bool) or max_input_chars <= 0:
            raise ValueError("max_input_chars must be positive")
        self.max_input_chars = max_input_chars

    async def compute(self, request: SemanticEmbeddingRequest, text: str) -> ComputedEmbedding:
        if request.model_binding != self.binding:
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        if not text.strip() or len(text) > self.max_input_chars:
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")
        remaining = (request.deadline_at - utcnow()).total_seconds()
        if remaining <= 0:
            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
        usage = "query" if request.usage == "Query" else "passage"
        payload = {
            "items": [
                {
                    "request_id": request.embedding_request_id,
                    "trace_id": request.trace_id,
                    "tenant_id": request.tenant_id,
                    "source_type": "semantic_compute",
                    "source_id": request.caller_request_ref,
                    "chunk_id": request.embedding_request_id,
                    "chunk_text": text,
                    "input_type": usage,
                    "preserve_input": True,
                    "embedding_required": True,
                }
            ]
        }
        response = await self.client.post("/v1/intercept", json=payload, timeout=remaining)
        if response.status_code != 200:
            raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED")
        data = response.json()
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED")
        result = results[0]
        if result.get("status") != "success":
            raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED")
        expected = {
            "request_id": request.embedding_request_id,
            "tenant_id": request.tenant_id,
            "source_id": request.caller_request_ref,
            "embedding_model": self.binding.model_id,
            "model_hash": self.expected_model_hash,
            "embedding_dim": self.binding.dimension,
            "schema_version": self.response_schema_version,
            "input_type": usage,
            "fallback_used": False,
        }
        if any(result.get(k) != v for k, v in expected.items()):
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        chunks = result.get("chunks")
        if not isinstance(chunks, list) or len(chunks) != 1 or not isinstance(chunks[0], dict):
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        chunk = chunks[0]
        if (
            chunk.get("chunk_text") != text
            or chunk.get("start_char") != 0
            or chunk.get("end_char") != len(text)
            or chunk.get("chunk_id") != request.embedding_request_id
        ):
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        evidence_ref: Identifier = uuid4().hex
        computed = ComputedEmbedding(
            vector=chunk.get("vector"),
            usage=request.usage,
            model_binding=self.binding,
            evidence_ref=evidence_ref,
        )
        with self.store.transaction() as tx:
            tx.put(
                "semantic-provider-evidence",
                request.tenant_id,
                evidence_ref,
                json.dumps(
                    {
                        "deployment_contract_ref": self.deployment_contract_ref,
                        "request": payload,
                        "response": result,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            )
        return computed
