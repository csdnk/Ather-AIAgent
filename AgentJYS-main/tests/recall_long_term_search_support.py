"""AET-35 real SDK Top-1 pressure evidence and independent assertion checks."""

import json
import math
from hashlib import sha256
from uuid import uuid4

from aether_agent_memory.recall.contracts.models import VectorCandidate, VectorSearchRequest
from aether_agent_memory.remember.contracts.models import ProjectionTarget
from azure_test_runtime import owned
from recall_working_search_support import required_search_filters, search_filter_structure

REVIEW_TEXT = "用户每周二上午参加项目评审；遇节假日顺延。"
REVIEW_QUERY = "项目评审通常安排在什么时候？"


def assert_top1_pressure(raw, unfiltered_top1, filtered_top1, target, axis):
    raw = [VectorCandidate.model_validate(value) for value in raw]
    top = [VectorCandidate.model_validate(value) for value in unfiltered_top1]
    filtered = [VectorCandidate.model_validate(value) for value in filtered_top1]
    assert len(raw) == 2 and [h.rank for h in raw] == [1, 2], "two ordered actual hits required"
    adverse, legal = raw
    assert legal.target == target and target.memory_source == "long_term"
    assert adverse.target.vector_id != target.vector_id
    assert len(top) == 1 and top[0].target == adverse.target and top[0].rank == 1
    assert len(filtered) == 1 and filtered[0].target == target and filtered[0].rank == 1
    assert all(h.score is not None and math.isfinite(h.score) for h in raw + top + filtered)
    assert adverse.score > legal.score > 0, "strictly adverse positive scores required"
    assert top[0].score == adverse.score and filtered[0].score == legal.score
    other = adverse.target
    assert all(t.generation and t.body_hash for t in (target, other))
    if axis == "scope":
        assert other.model_space == target.model_space and other.memory_source == "long_term"
        assert other.memory.scope.session_id != target.memory.scope.session_id
        assert other.memory.scope.model_dump(exclude={"session_id"}) == (
            target.memory.scope.model_dump(exclude={"session_id"})
        )
    elif axis == "space":
        assert other.memory.scope == target.memory.scope and other.memory_source == "long_term"
        assert other.model_space != target.model_space
    else:
        assert axis == "source"
        assert other.memory.scope == target.memory.scope and other.model_space == target.model_space
        assert other.memory_source == "working"
    return {
        "axis": axis,
        "target_vector_id": target.vector_id,
        "adverse_vector_id": other.vector_id,
        "legal_score": legal.score,
        "adverse_score": adverse.score,
        "unfiltered_top1": top[0].target.vector_id,
        "filtered_top1": filtered[0].target.vector_id,
    }


def assert_top1_requests(pair, raw_top1, filtered, target, adverse_id, vector, collection):
    pair_filter = "vector_id in " + json.dumps([target.vector_id, adverse_id])
    for request, limit in ((pair, 2), (raw_top1, 1), (filtered, 1)):
        assert request["limit"] == limit, "actual backend K differs"
        assert request["data"] == [list(vector)], "probe used a different query vector"
        assert request["collection_name"] == collection, "probe used a different collection"
        assert request["anns_field"] == "vector" and request["search_params"] == {
            "metric_type": "IP"
        }
        assert request["consistency_level"] == "Strong"
    assert pair["filter"] == raw_top1["filter"] == pair_filter
    operator, filters = search_filter_structure(filtered["filter"])
    assert operator == "and"
    required = required_search_filters(filtered["filter"])
    assert "model_space == " + json.dumps(target.model_space) in required
    source = '(not exists target["memory_source"] or target["memory_source"] == "long_term")'
    assert source in filters, "long-term filter must constrain every alternative"
    for key, value in target.memory.scope.model_dump(exclude_none=True).items():
        assert f"{key} == " + json.dumps(value, ensure_ascii=True) in required, "scope bypass"
    return {
        "collection": collection,
        "unfiltered_limit": 1,
        "filtered_limit": 1,
        "pair_filter": pair_filter,
        "filtered_expression": filtered["filter"],
    }


def assert_owned_index(vectors):
    resources = owned()
    assert any(client is vectors for client in resources.clients), "test-owned provider required"
    assert vectors.namespace in resources.provider_namespaces.values()
    assert vectors.namespace.startswith("test-")


async def published_target(vectors, ctx, memory):
    rows = await vectors.call(
        ctx,
        "query",
        filter='target["memory"]["memory_id"] == ' + json.dumps(memory.ref.memory_id),
        output_fields=["target"],
        limit=100,
        consistency_level="Strong",
    )
    assert len(rows) == 1, "fixture requires one actual published chunk"
    target = ProjectionTarget.model_validate(rows[0]["target"])
    assert target.memory == memory.ref and target.body_hash == memory.content_hash
    assert target.model_space == memory.model_space and target.generation
    assert target.memory_source == ("working" if memory.kind == "working" else "long_term")
    return target


def index_pressure_row(target, vector, axis):
    """Explicit index contamination: never a fabricated Ready body or compute result."""
    data = target.model_dump(mode="json")
    data["vector_id"] = sha256((axis + uuid4().hex).encode()).hexdigest()
    if axis == "space":
        data["model_space"] = "wrong-space-" + uuid4().hex
    adverse = ProjectionTarget.model_validate(data)
    return adverse, {
        "vector_id": adverse.vector_id,
        "vector": list(vector),
        "target": adverse.model_dump(mode="json"),
        "model_space": adverse.model_space,
        **{key: value or "" for key, value in adverse.memory.scope.model_dump().items()},
    }


def sdk_candidates(hits):
    values = []
    for rank, hit in enumerate(hits[0] if hits else [], 1):
        target = ProjectionTarget.model_validate(hit["entity"]["target"])
        assert str(hit["id"]) == target.vector_id
        values.append(
            {
                "target": target.model_dump(mode="json"),
                "score": float(hit["distance"]),
                "rank": rank,
            }
        )
    return values


async def collect_pressure(vectors, probe, ctx, vector, target, adverse, axis):
    assert_owned_index(vectors)
    pair_filter = "vector_id in " + json.dumps([target.vector_id, adverse.vector_id])
    arguments = {
        "data": [list(vector)],
        "anns_field": "vector",
        "filter": pair_filter,
        "output_fields": ["target"],
        "search_params": {"metric_type": "IP"},
        "consistency_level": "Strong",
    }
    raw = await vectors.call(ctx, "search", limit=2, **arguments)
    raw_top = await vectors.call(ctx, "search", limit=1, **arguments)
    request = VectorSearchRequest(
        selection={"session_id": target.memory.scope.session_id},
        memory_source="long_term",
        model_space=target.model_space,
        vector=vector,
        limit=1,
        deadline_at=ctx.deadline_at,
    )
    result = await vectors.search(ctx, request)
    assert result.coverage == "complete" and len(result.candidates) == 1
    assert result.candidates[0].target == target
    row = next(r for r in probe.searches if r["operation_id"] == ctx.operation_id)
    assert len(row["sdk"]) == len(row["sdk_results"]) == 1
    pressure = assert_top1_pressure(
        sdk_candidates(raw),
        sdk_candidates(raw_top),
        sdk_candidates(row["sdk_results"][0]),
        target,
        axis,
    )
    pair_calls = [
        c["request"] for c in probe.sdk_calls if c["request"].get("filter") == pair_filter
    ]
    assert len(pair_calls) == 2
    pressure.update(
        assert_top1_requests(
            next(c for c in pair_calls if c["limit"] == 2),
            next(c for c in pair_calls if c["limit"] == 1),
            row["sdk"][0],
            target,
            adverse.vector_id,
            vector,
            vectors.collection,
        )
    )
    pressure.update(
        operation_id=ctx.operation_id,
        trace_id=ctx.trace_id,
        vector_hash=sha256(json.dumps(list(vector)).encode()).hexdigest(),
    )
    return pressure
