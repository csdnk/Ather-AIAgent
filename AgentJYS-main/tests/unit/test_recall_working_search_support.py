"""Strict assertion checks; these do not establish native/backend acceptance."""

from types import SimpleNamespace

import pytest

from recall_working_search_support import WorkingSearchProbe, assert_execution, assert_source_pack


def empty_pack():
    return {
        "recall_id": "empty-recall",
        "scope": {"tenant_id": "t1", "application_id": "app", "user_id": "u", "agent_id": "a"},
        "outcome": "empty",
        "selected_sources": ["working"],
        "coverage": {"working": "complete", "long_term": "not_requested"},
        "groups": [],
        "rendered_context": "",
        "token_budget": 1000,
        "tokens_used": 0,
        "tokenizer_id": "test-tokenizer",
        "policy_version": "test-policy",
        "degradation_reasons": [],
        "committed_at": "2026-10-09T00:00:00.000Z",
    }


def test_normal_working_empty_requires_complete_coverage_without_long_term():
    assert_source_pack(empty_pack(), "working", ())
    raw = empty_pack()
    raw.update(selected_sources=["working", "long_term"])
    raw["coverage"]["long_term"] = "complete"
    with pytest.raises(AssertionError, match="source"):
        assert_source_pack(raw, "working", ())


@pytest.mark.parametrize("fault", [None, "truncated", "version", "provenance", "compressed"])
def test_authoritative_body_ref_and_provenance_must_survive_packing(fault):
    from aether_agent_memory.remember.contracts.models import MemoryRef, SourceRef
    from unit.test_recall_tenant_isolation_support import pack

    payload = pack(content="用户喝咖啡不加糖。")
    item = payload["groups"][0]["items"][0]
    memory = SimpleNamespace(
        ref=MemoryRef.model_validate(item["memory"]),
        content="用户喝咖啡不加糖。",
        sources=tuple(SourceRef.model_validate(s) for s in item["sources"]),
    )
    if fault is None:
        assert_source_pack(payload, "working", (memory,))
        return
    if fault == "truncated":
        item["content"] = "用户喝咖啡"
    elif fault == "version":
        item["memory"]["version"] = 2
    elif fault == "provenance":
        item["sources"][0]["source_id"] = "foreign-source"
    else:
        item.update(representation="compressed", artifact_id="compressed-artifact")
    with pytest.raises(AssertionError):
        assert_source_pack(payload, "working", (memory,))


async def test_probe_forwards_real_boundary_returns_and_records_failed_search(monkeypatch):
    ctx = SimpleNamespace(operation_id="original-operation", trace_id="a" * 32)
    computed = SimpleNamespace(vector=(1.0, 0.0), evidence_ref="actual-compute-evidence")

    async def compute(request, text):
        assert text == "query"
        return computed

    backend = SimpleNamespace(compute=compute)

    async def embed(context, request):
        assert context is ctx
        return await backend.compute(request, request.texts[0])

    def sdk_search(**kwargs):
        assert kwargs["filter"] == 'target["memory_source"] == "long_term"'
        raise OSError("real delegate failure")

    sdk = SimpleNamespace(search=sdk_search)

    async def search(context, request):
        sdk.search(filter='target["memory_source"] == "long_term"', data=[[1.0, 0.0]])

    runtime = SimpleNamespace(
        native_embedding=SimpleNamespace(backends={"Query": backend}),
        embedding=SimpleNamespace(embed=embed),
        vectors=SimpleNamespace(search=search, client=sdk),
    )
    probe = WorkingSearchProbe(runtime, monkeypatch)
    request = SimpleNamespace(usage="query", texts=("query",), model_space="space")
    assert await runtime.embedding.embed(ctx, request) is computed
    request = SimpleNamespace(memory_source="long_term")
    with pytest.raises(OSError, match="real delegate failure"):
        await runtime.vectors.search(ctx, request)
    assert len(probe.embeddings) == len(probe.searches) == 1
    assert probe.embeddings[0]["native"][0]["result"] is computed
    assert probe.searches[0]["result"] is None
    assert len(probe.searches[0]["sdk"]) == 1
    assert probe.searches[0]["operation_id"] == "original-operation"


def observed_execution():
    from hashlib import sha256

    from aether_agent_memory.runtime.contracts.models import ScopeSelector

    space = SimpleNamespace(model_space="actual-space", dimensions=2)
    binding = "actual-model-binding"
    native = SimpleNamespace(space=space, binding=binding)
    result = SimpleNamespace(
        operation_id="encoding",
        usage="query",
        model_space=space.model_space,
        limit=20,
        dimensions=2,
        items=(SimpleNamespace(input_hash=sha256(b"query").hexdigest(), vector=(1.0, 0.0)),),
    )
    query = {
        "operation_id": "operation",
        "trace_id": "a" * 32,
        "request": SimpleNamespace(
            operation_id="encoding",
            usage="query",
            texts=("query",),
            model_space=space.model_space,
        ),
        "result": result,
        "native": [
            {
                "input_hash": sha256(b"query").hexdigest(),
                "request": SimpleNamespace(usage="Query", trace_id="a" * 32, model_binding=binding),
                "result": SimpleNamespace(
                    usage="Query",
                    model_binding=binding,
                    vector=(1.0, 0.0),
                    evidence_ref="compute-1",
                ),
            }
        ],
    }
    search = {
        "operation_id": "operation",
        "trace_id": "a" * 32,
        "request": SimpleNamespace(
            memory_source="working",
            model_space=space.model_space,
            selection=ScopeSelector(session_id="s1"),
            vector=(1.0, 0.0),
        ),
        "result": SimpleNamespace(coverage="complete", candidates=()),
        "sdk": [
            {
                "filter": 'model_space == "actual-space" and target["memory_source"] == "working"'
                ' and session_id == "s1"',
                "data": [[1.0, 0.0]],
                "collection_name": "owned-collection",
                "search_params": {"metric_type": "IP"},
            }
        ],
    }
    return SimpleNamespace(embeddings=[query], searches=[search]), native


@pytest.mark.parametrize("source", ["working", "long_term"])
def test_actual_native_vector_and_sdk_filter_are_bound_to_original_operation(source):
    import json

    probe, native = observed_execution()
    if source == "long_term":
        probe.searches[0]["request"].memory_source = "long_term"
        probe.searches[0]["sdk"][0]["filter"] = (
            '(session_id == "s1") and model_space == "actual-space" and '
            '(not exists target["memory_source"] or target["memory_source"] == "long_term")'
        )
    evidence = assert_execution(probe, "operation", "query", native, source, ())
    assert evidence["long_term_calls"] == (1 if source == "long_term" else 0)
    assert evidence["native_evidence_refs"] == ["compute-1"]
    assert evidence["search_requests"][0]["sdk"][0]["collection"] == "owned-collection"
    json.dumps(evidence)


def test_archived_raw_hit_can_be_rejected_without_confusing_normal_empty_with_no_hits():
    from aether_agent_memory.recall.contracts.models import VectorCandidate
    from unit.test_recall_tenant_isolation_support import hit

    probe, native = observed_execution()
    raw = hit("T-A", 0.9, 1)
    raw["target"]["model_space"] = native.space.model_space
    candidate = VectorCandidate.model_validate(raw)
    probe.searches[0]["result"].candidates = (candidate,)
    with pytest.raises(AssertionError, match="unexpected fixture Refs"):
        assert_execution(probe, "operation", "query", native, "working", ())
    assert_execution(probe, "operation", "query", native, "working", (), (candidate.target.memory,))
    assert_source_pack(empty_pack(), "working", ())


@pytest.mark.parametrize(
    "fault",
    [
        "no-native",
        "no-sdk",
        "wrong-filter",
        "bypassing-or",
        "bypassing-selection",
        "wrong-vector",
        "long-term-attempt",
        "other-operation",
        "other-query",
        "incomplete",
    ],
)
def test_non_native_or_fallback_evidence_is_rejected(fault):
    probe, native = observed_execution()
    query, search = probe.embeddings[0], probe.searches[0]
    if fault == "no-native":
        query["native"] = []
    elif fault == "no-sdk":
        search["sdk"] = []
    elif fault == "wrong-filter":
        search["sdk"][0]["filter"] = 'model_space == "actual-space"'
    elif fault == "bypassing-or":
        search["sdk"][0]["filter"] = "(" + search["sdk"][0]["filter"] + ") or true"
    elif fault == "bypassing-selection":
        search["sdk"][0]["filter"] = (
            '(session_id == "s1" or true) and model_space == "actual-space" and '
            'target["memory_source"] == "working"'
        )
    elif fault == "wrong-vector":
        search["sdk"][0]["data"] = [[0.0, 1.0]]
    elif fault == "long-term-attempt":
        probe.searches.append({**search, "request": SimpleNamespace(memory_source="long_term")})
    elif fault == "other-operation":
        query["operation_id"] = "foreign-operation"
    elif fault == "other-query":
        query["request"].texts = ("foreign query",)
    else:
        search["result"].coverage = "partial"
    with pytest.raises(AssertionError):
        assert_execution(probe, "operation", "query", native, "working", ())
