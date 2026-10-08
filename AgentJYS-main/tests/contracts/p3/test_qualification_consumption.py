"""AET-16 RC-QUA-01–04: consume B evidence through Recall's qualification port.

Contract fixtures and a controlled B provider need no storage. Search and actual
Remember authority are exercised separately in test_recall_qualification.py.
"""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from test_qualification_contracts import evidence, qualification

from aether_agent_memory.recall.basic.candidates import MemoryCandidates
from aether_agent_memory.recall.contracts.foundation import (
    ChunkHit,
    EmbeddingSpace,
    MemorySearchRequest,
)
from aether_agent_memory.recall.embedding.spaces import EmbeddingSpaces
from aether_agent_memory.remember.contracts.foundation import CandidateQualificationResult
from aether_agent_memory.runtime.contracts.models import TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError


class QualificationProvider:
    def __init__(self, values):
        self.values = values
        self.calls = []

    async def qualify(self, ctx, targets, purpose):
        self.calls.append((ctx, targets, purpose))
        return self.values


class MalformedEvidence:
    """An encoded B response that intentionally violates the wire contract."""

    def __init__(self, payload):
        self.payload = payload

    def model_dump_json(self):
        return json.dumps(self.payload)


@pytest.fixture
def consumer():
    candidate = evidence()
    hit = ChunkHit.model_validate(candidate["hits"][0])
    ctx = TrustedContext(
        principal={
            "principal_id": "reader",
            "home_scope": hit.memory.scope,
            "permissions": ["memory:read"],
            "auth_epoch": 1,
        },
        request_id="request",
        operation_id="search",
        trace_id="a" * 32,
        span_id="a" * 16,
        deadline_at="2026-09-21T02:02:00.000Z",
    )
    space = EmbeddingSpace(
        model_space="space_1",
        model_id="test",
        model_revision="v1",
        dimensions=3,
        tokenizer_id="embed_tok_1",
        query_prefix="",
        passage_prefix="",
        normalization="none",
        metric="inner_product",
        max_input_tokens=512,
    )
    request = MemorySearchRequest(
        operation_id="search",
        purpose="recall",
        query="query",
        selection={},
        model_space=space.model_space,
        memory_top_k=2,
        chunk_page_size=10,
        max_chunk_hits=50,
        max_rounds=5,
        deadline_at=ctx.deadline_at,
    )
    provider = QualificationProvider((CandidateQualificationResult(**qualification()),))
    # qualify uses only the clock and B port; unused storage/search ports are absent.
    search = MemoryCandidates(
        None,
        SimpleNamespace(clock=lambda: "2026-09-21T02:00:01.000Z"),
        None,
        None,
        provider,
        EmbeddingSpaces((space,)),
    )
    return search, provider, ctx, (hit,), request


@pytest.mark.p0
@pytest.mark.parametrize(
    "fault",
    [
        "manifest",
        "guard",
        "body_hash",
        "version",
        "scope",
        "generation",
        "model_space",
        "chunk_index",
        "vector_id",
        "input_hash",
        "unverified",
        "unpublished",
    ],
)
def test_rc_qua_02_invalid_allowed_evidence_is_a_contract_error(consumer, fault):
    search, provider, ctx, hits, request = consumer
    payload = copy.deepcopy(qualification())
    if fault in {"manifest", "guard"}:
        payload[fault] = None
    elif fault == "body_hash":
        payload["guard"]["body_hash"] = "f" * 64
    elif fault == "version":
        payload["guard"]["memory"]["version"] = 2
    elif fault == "scope":
        payload["guard"]["memory"]["scope"]["tenant_id"] = "other"
    elif fault in {"generation", "model_space"}:
        payload["target"][fault] = "other"
    elif fault == "chunk_index":
        payload["target"][fault] = 99
    elif fault in {"vector_id", "input_hash"}:
        payload["target"][fault] = "f" * 64
    elif fault == "unverified":
        payload["manifest"]["chunks"][0]["verified"] = False
    else:
        payload["manifest"]["published_at"] = None
    provider.values = (MalformedEvidence(payload),)
    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))
    assert failure.value.code == "CONTRACT_VIOLATION"


@pytest.mark.p0
@pytest.mark.parametrize("purpose", ["recall", "extraction"])
def test_rc_qua_01_exact_target_and_complete_evidence(consumer, purpose):
    search, provider, ctx, hits, request = consumer
    request = request.model_copy(update={"purpose": purpose})
    result = asyncio.run(search.qualify(ctx, hits, request))
    passed_ctx, targets, passed_purpose = provider.calls[0]
    assert passed_ctx == ctx and passed_purpose == purpose
    assert targets[0].model_dump(mode="json") == {
        "memory": hits[0].memory.model_dump(mode="json"),
        "generation": "gen_1",
        "model_space": "space_1",
        "body_hash": qualification()["target"]["body_hash"],
        "chunk_index": 0,
        "vector_id": "a" * 64,
        "input_hash": qualification()["target"]["input_hash"],
        "memory_source": "long_term",
    }
    assert len(result) == 1
    proof = next(iter(result.values()))
    assert proof.decision == "allowed"
    assert proof.manifest == provider.values[0].manifest
    assert proof.guard == provider.values[0].guard


@pytest.mark.p0
@pytest.mark.parametrize("decision", ["excluded", "unverifiable"])
@pytest.mark.parametrize("attachment", ["manifest", "guard", "both"])
def test_rc_qua_03_non_allowed_evidence_fails_closed(consumer, decision, attachment):
    search, provider, ctx, hits, request = consumer
    payload = qualification()
    payload["decision"] = decision
    if attachment == "manifest":
        payload["guard"] = None
    elif attachment == "guard":
        payload["manifest"] = None
    provider.values = (MalformedEvidence(payload),)
    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))
    assert failure.value.code == "CONTRACT_VIOLATION"


@pytest.mark.p0
@pytest.mark.parametrize("fault", ["missing", "duplicate", "extra", "wrong_target"])
def test_rc_qua_04_results_cover_exactly_the_requested_targets(consumer, fault):
    search, provider, ctx, hits, request = consumer
    valid = provider.values[0]
    payload = qualification()
    for field in ("target", "manifest", "guard"):
        payload[field]["memory"]["memory_id"] = "unrequested"
    other = CandidateQualificationResult.model_validate(payload)
    provider.values = {
        "missing": (),
        "duplicate": (valid, valid),
        "extra": (valid, other),
        "wrong_target": (other,),
    }[fault]
    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))
    assert failure.value.code == "CONTRACT_VIOLATION"


@pytest.mark.p0
def test_rc_qua_04_response_order_is_not_a_binding_requirement(consumer):
    search, provider, ctx, hits, request = consumer
    payload = qualification()
    for field in ("target", "manifest", "guard"):
        payload[field]["memory"]["memory_id"] = "second"
    second = CandidateQualificationResult.model_validate(payload)
    provider.values = (second, provider.values[0])
    second_hit = hits[0].model_copy(update={"memory": second.target.memory})
    result = asyncio.run(search.qualify(ctx, (*hits, second_hit), request))
    assert {r.target.memory.memory_id for r in result.values()} == {"id_1", "second"}
