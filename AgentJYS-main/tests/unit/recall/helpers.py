from __future__ import annotations

import asyncio
from datetime import timedelta

from aether_agent_memory.recall.admission import (
    RecallAuthorization,
    RecallInput,
    RecallPolicy,
)
from aether_agent_memory.recall.embedding.models import EmbeddingModelBinding
from aether_agent_memory.recall.embedding.service import (
    ComputedEmbedding,
    EmbeddingRuntime,
    ResolvedEmbeddingInput,
    make_embedding_request,
)
from aether_agent_memory.runtime.contract_types import Scope, hash_json, hash_text, utcnow


def binding():
    return EmbeddingModelBinding(
        model_id="test-model",
        model_version="v1",
        dimension=3,
        dtype="float32",
        embedding_schema_version="e1",
        preprocessing_version="p1",
        retrieval_space_ref="space-v1",
        model_contract_ref="contract-v1",
    )


def scope(**changes):
    return Scope(
        tenant_id="tenant", project_id=None, agent_id="agent", session_id="session", task_id=None
    ).replaced(**changes)


def policy(**changes):
    return RecallPolicy(
        tokenizer_id="tokens",
        tokenizer_version="v1",
        template_version="v1",
        retrieval_space_ref="space-v1",
    ).replaced(**changes)


def recall_input(**changes):
    return RecallInput(
        request_id="request",
        trace_id="trace",
        query="原始问题",
        scope=scope(),
        principal_ref="principal",
        authorization_ref="auth",
        idempotency_key="key",
        deadline_at=utcnow() + timedelta(seconds=5),
        token_budget=128,
        tokenizer_id="tokens",
        tokenizer_version="v1",
        template_version="v1",
    ).replaced(**changes)


class Authority:
    working = True
    long_term = True
    valid = True

    async def authorize(self, request):
        return RecallAuthorization(
            scope=request.scope,
            principal_ref=request.principal_ref,
            evidence_ref=request.authorization_ref,
            scope_valid=self.valid,
            working_read=self.working,
            long_term_read=self.long_term,
            valid_until=utcnow() + timedelta(seconds=20),
        )


class Inputs:
    allowed = True

    def __init__(self):
        self.values = {}

    async def authorize(self, request):
        return self.allowed

    async def resolve(self, request):
        text, digest = self.values[request.input_ref]
        return ResolvedEmbeddingInput(text=text, input_binding_digest=digest)

    def request(self, text="原始输入", usage="Query", caller_request_ref="call", **changes):
        digest = hash_json({"input": text, "chunk": "fixed-chunk"})
        ref = "input-" + hash_text(text).value
        self.values[ref] = (text, digest)
        return make_embedding_request(
            tenant_id="tenant",
            caller_ref="caller",
            caller_request_ref=caller_request_ref,
            trace_id="trace",
            authorization_ref="auth",
            usage=usage,
            input_ref=ref,
            source_hash=hash_text(text),
            input_binding_digest=digest,
            input_binding_ref="approved-input",
            model_binding=binding(),
            deadline_at=utcnow() + timedelta(seconds=5),
            execution_policy_ref="embedding-policy-0.1",
        ).replaced(**changes)


class Backend:
    """Test-only deterministic backend; never registered by application bootstrap."""

    def __init__(self, *, gate=None, vector=None, wrong_binding=False):
        self.calls = 0
        self.gate = gate
        self.started = asyncio.Event()
        self.vector = [1.0, 2.0, 3.0] if vector is None else vector
        self.wrong_binding = wrong_binding
        self.texts = []

    async def compute(self, request, text):
        self.calls += 1
        self.texts.append(text)
        self.started.set()
        if self.gate:
            await self.gate.wait()
        return ComputedEmbedding(
            vector=self.vector,
            usage=request.usage,
            model_binding=binding().replaced(model_version="wrong")
            if self.wrong_binding
            else binding(),
            evidence_ref="test-backend-evidence",
        )


def runtime(backend):
    return EmbeddingRuntime(
        binding=binding(),
        backend=backend,
        count_tokens=lambda text, usage: len(text),
        max_input_tokens=128,
    )
