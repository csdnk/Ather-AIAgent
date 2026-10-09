"""AET-34 observations at real native/VectorSearch/SDK boundaries.

Wrappers always call the installed provider. No generated hits, database inspection,
or new product diagnostic routes. Only allowlisted digests are persisted.
"""

import json
import math
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256

from aether_agent_memory.recall.contracts.models import ContextPack

QUERY = "用户的咖啡加糖习惯是什么？"
F2_TEXT = "用户长期保持喝咖啡不加糖的习惯。"


class WorkingSearchProbe:
    def __init__(self, runtime, monkeypatch):
        self.runtime = runtime
        self.embeddings, self.searches = [], []
        self.sdk_calls = []
        active_embedding = ContextVar("working_probe_embedding", default=None)
        active_search = ContextVar("working_probe_search", default=None)
        embed = runtime.embedding.embed
        compute = runtime.native_embedding.backends["Query"].compute
        search = runtime.vectors.search
        sdk_search = runtime.vectors.client.search

        async def observed_embed(ctx, request):
            if request.usage != "query":
                return await embed(ctx, request)
            row = dict(
                operation_id=ctx.operation_id,
                trace_id=ctx.trace_id,
                request=request,
                result=None,
                native=[],
            )
            self.embeddings.append(row)
            token = active_embedding.set(row)
            try:
                row["result"] = await embed(ctx, request)
                return row["result"]
            finally:
                active_embedding.reset(token)

        async def observed_compute(request, text):
            row = active_embedding.get()
            call = dict(request=request, input_hash=sha256(text.encode()).hexdigest(), result=None)
            if row is not None:
                row["native"].append(call)
            call["result"] = await compute(request, text)
            return call["result"]

        async def observed_search(ctx, request):
            row = dict(
                operation_id=ctx.operation_id,
                trace_id=ctx.trace_id,
                request=request,
                result=None,
                sdk=[],
            )
            # Count attempts before awaiting, including failures and cancelled requests.
            self.searches.append(row)
            token = active_search.set(row)
            try:
                row["result"] = await search(ctx, request)
                return row["result"]
            finally:
                active_search.reset(token)

        def observed_sdk(*args, **kwargs):
            row = active_search.get()
            call = {"request": deepcopy(kwargs), "result": None}
            self.sdk_calls.append(call)
            if row is not None:
                row["sdk"].append(call["request"])
            result = sdk_search(*args, **kwargs)
            call["result"] = result
            if row is not None:
                row.setdefault("sdk_results", []).append(result)
            return result

        monkeypatch.setattr(runtime.embedding, "embed", observed_embed)
        monkeypatch.setattr(runtime.native_embedding.backends["Query"], "compute", observed_compute)
        monkeypatch.setattr(runtime.vectors, "search", observed_search)
        monkeypatch.setattr(runtime.vectors.client, "search", observed_sdk)


def assert_source_pack(payload, source, memories):
    pack = ContextPack.model_validate(payload)
    assert pack.selected_sources == (source,), "unexpected source contribution"
    assert getattr(pack.coverage, source) == "complete"
    other = "long_term" if source == "working" else "working"
    assert getattr(pack.coverage, other) == "not_requested"
    assert not pack.degradation_reasons
    items = [item for group in pack.groups for item in group.items]
    assert {item.memory for item in items} == {m.ref for m in memories}, "unexpected result Ref"
    assert pack.outcome == ("available" if memories else "empty")
    for item in items:
        expected = next(m for m in memories if m.ref == item.memory)
        assert item.content == expected.content, "authoritative body changed"
        assert item.representation == "original", "full original body required"
        assert item.sources == expected.sources, "source provenance changed"
        assert item.content in pack.rendered_context
    return pack


def search_filter_structure(expression):
    """Read boolean structure without treating quoted values as operators."""
    expression = expression.strip()
    conjunctions, alternatives = [], []
    and_start = or_start = depth = 0
    quote, escaped, outer_end = None, False, None
    for index, char in enumerate(expression):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            assert depth >= 0, "unbalanced SDK filter"
            if depth == 0 and outer_end is None:
                outer_end = index
        elif depth == 0:
            if expression[index : index + 4] == " or ":
                alternatives.append(expression[or_start:index].strip())
                or_start = index + 4
            if expression[index : index + 5] == " and ":
                conjunctions.append(expression[and_start:index].strip())
                and_start = index + 5
    assert depth == 0 and quote is None, "unbalanced SDK filter"
    if alternatives:
        return "or", (*alternatives, expression[or_start:].strip())
    if conjunctions:
        return "and", (*conjunctions, expression[and_start:].strip())
    if expression.startswith("(") and outer_end == len(expression) - 1:
        return search_filter_structure(expression[1:-1])
    return "atom", (expression,)


def required_search_filters(expression):
    operator, parts = search_filter_structure(expression)
    if operator == "atom":
        return set(parts)
    constraints = [required_search_filters(part) for part in parts]
    return set.intersection(*constraints) if operator == "or" else set.union(*constraints)


def assert_execution(
    probe, operation_id, query, native, source, expected_refs, allowed_discovery_refs=()
):
    encodings = [r for r in probe.embeddings if r["operation_id"] == operation_id]
    searches = [r for r in probe.searches if r["operation_id"] == operation_id]
    assert encodings and searches, "actual query encoding and vector search required"
    assert all(r["request"].memory_source == source for r in searches), "cross-source attempt"
    traces = {r["trace_id"] for r in encodings + searches}
    assert len(traces) == 1, "original trace binding differs"
    digest = sha256(query.encode()).hexdigest()
    space = native.space
    vectors, native_evidence = [], []
    for row in encodings:
        request, result = row["request"], row["result"]
        assert request.usage == "query" and request.texts == (query,)
        assert result is not None and result.operation_id == request.operation_id
        assert (
            result.usage == "query"
            and result.model_space == request.model_space == space.model_space
        )
        assert result.dimensions == space.dimensions and len(result.items) == 1
        vector = tuple(result.items[0].vector)
        assert result.items[0].input_hash == digest and len(vector) == space.dimensions
        assert all(math.isfinite(v) for v in vector)
        assert abs(sum(v * v for v in vector) - 1) < 1e-4
        assert len(row["native"]) == 1, "actual native Query computation required"
        call = row["native"][0]
        computed = call["result"]
        assert call["input_hash"] == digest and computed is not None
        assert call["request"].trace_id == row["trace_id"]
        assert call["request"].usage == computed.usage == "Query"
        assert call["request"].model_binding == computed.model_binding == native.binding
        assert tuple(computed.vector) == vector and computed.evidence_ref
        native_evidence.append(computed.evidence_ref)
        vectors.append(vector)
    candidates = set()
    sdk_count, search_evidence = 0, []
    for row in searches:
        request, result = row["request"], row["result"]
        assert request.model_space == space.model_space and tuple(request.vector) in vectors
        assert result is not None and result.coverage == "complete"
        assert row["sdk"], "actual Milvus SDK search required"
        sdk_evidence = []
        for sdk in row["sdk"]:
            sdk_count += 1
            assert sdk["data"] == [list(request.vector)], "SDK did not search the query vector"
            expression = sdk["filter"]
            operator, filters = search_filter_structure(expression)
            assert operator == "and", "SDK filter permits a bypass"
            required = required_search_filters(expression)
            assert "model_space == " + json.dumps(space.model_space) in required
            source_filter = (
                'target["memory_source"] == "working"'
                if source == "working"
                else (
                    '(not exists target["memory_source"] or target["memory_source"] == "long_term")'
                )
            )
            assert source_filter in filters, "source must constrain every search alternative"
            other = "long_term" if source == "working" else "working"
            assert f'target["memory_source"] == "{other}"' not in expression
            for key, value in request.selection.model_dump(exclude_none=True).items():
                assert f"{key} == " + json.dumps(value, ensure_ascii=True) in required
            sdk_evidence.append(
                {
                    "filter": expression,
                    "collection": sdk["collection_name"],
                    "metric": sdk["search_params"]["metric_type"],
                }
            )
        for candidate in result.candidates:
            target = candidate.target
            assert target.memory_source == source and target.model_space == space.model_space
            assert target.generation and target.body_hash
            candidates.add(target.memory)
        search_evidence.append(
            {
                "source": request.memory_source,
                "model_space": request.model_space,
                "selection": request.selection.model_dump(mode="json"),
                "vector_hash": sha256(json.dumps(list(request.vector)).encode()).hexdigest(),
                "sdk": sdk_evidence,
                "candidate_count": len(result.candidates),
                "discovery_limit": request.limit,
            }
        )
    assert set(expected_refs).issubset(candidates), "required fixture Refs missing"
    assert candidates <= set(expected_refs) | set(allowed_discovery_refs), "unexpected fixture Refs"
    return {
        "operation_id": operation_id,
        "trace_id": next(iter(traces)),
        "query_hash": digest,
        "model_space": space.model_space,
        "query_calls": len(encodings),
        "vector_calls": len(searches),
        "sdk_calls": sdk_count,
        "long_term_calls": sum(r["request"].memory_source == "long_term" for r in searches),
        "working_calls": sum(r["request"].memory_source == "working" for r in searches),
        "native_evidence_refs": native_evidence,
        "search_requests": search_evidence,
    }
