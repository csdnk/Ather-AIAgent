"""Fault lifecycle and assertion regressions, not native-chain acceptance."""

import asyncio
from types import SimpleNamespace

import pytest

from aether_agent_memory.remember.contracts.models import MemoryRef, SourceRef
from recall_source_failure_support import SourceFault, assert_both_empty, assert_partial_pack
from unit.test_recall_tenant_isolation_support import pack


@pytest.mark.parametrize("kind", ["timeout", "unavailable"])
async def test_fault_crosses_sdk_thread_only_for_selected_source_and_session(monkeypatch, kind):
    calls = []

    def sdk_search(**kwargs):
        calls.append(kwargs)
        return "actual SDK result"

    sdk = SimpleNamespace(search=sdk_search)

    async def search(ctx, request):
        return await asyncio.to_thread(sdk.search, filter="actual-filter", collection_name="owned")

    runtime = SimpleNamespace(vectors=SimpleNamespace(search=search, client=sdk))
    ctx = SimpleNamespace(operation_id="original-operation", trace_id="a" * 32)
    request = SimpleNamespace(
        memory_source="long_term", selection=SimpleNamespace(session_id="owned")
    )
    fault = SourceFault(runtime, "owned", "long_term", kind)
    with pytest.raises(AssertionError, match="test interruption"), fault.installed(monkeypatch):
        for _ in range(2):
            with pytest.raises(TimeoutError if kind == "timeout" else OSError):
                await runtime.vectors.search(ctx, request)
        assert len(fault.attempts) == 2 and not calls
        assert all(row["operation_id"] == ctx.operation_id for row in fault.attempts)
        request.memory_source = "working"
        assert await runtime.vectors.search(ctx, request) == "actual SDK result"
        request.memory_source = "long_term"
        request.selection.session_id = "outside"
        assert await runtime.vectors.search(ctx, request) == "actual SDK result"
        assert len(calls) == 2 and len(fault.attempts) == 2
        raise AssertionError("test interruption")
    assert runtime.vectors.search is search and runtime.vectors.client.search is sdk_search
    request.selection.session_id = "owned"
    assert await runtime.vectors.search(ctx, request) == "actual SDK result"


def degraded():
    payload = pack(content="完整的合法正文。")
    payload.update(
        outcome="degraded",
        selected_sources=["working", "long_term"],
        degradation_reasons=["long_term_dependency"],
    )
    payload["coverage"]["long_term"] = "unavailable"
    item = payload["groups"][0]["items"][0]
    memory = SimpleNamespace(
        ref=MemoryRef.model_validate(item["memory"]),
        content=item["content"],
        sources=tuple(SourceRef.model_validate(source) for source in item["sources"]),
    )
    failed_memory = SimpleNamespace(
        ref=memory.ref.model_copy(update={"memory_id": "failed-source-ref"}),
        content=memory.content,
    )
    return payload, memory, failed_memory


def test_identical_plaintext_does_not_authorize_a_failed_source_ref():
    payload, ready, failed = degraded()
    assert_partial_pack(payload, (ready,), "long_term", (failed,), "test")
    payload["groups"][0]["items"][0]["memory"] = failed.ref.model_dump(mode="json")
    with pytest.raises(AssertionError):
        assert_partial_pack(payload, (ready,), "long_term", (failed,), "test")


@pytest.mark.parametrize(
    "fault",
    ["complete-both", "wrong-source", "wrong-reason", "truncated", "expired-fill", "version"],
)
def test_partial_pack_rejects_false_success_or_unsafe_filler(fault):
    payload, ready, failed = degraded()
    if fault == "complete-both":
        payload["coverage"]["long_term"] = "complete"
    elif fault == "wrong-source":
        payload["selected_sources"] = ["working"]
        payload["coverage"]["long_term"] = "not_requested"
    elif fault == "wrong-reason":
        payload["degradation_reasons"] = ["working_dependency"]
    elif fault == "truncated":
        payload["groups"][0]["items"][0]["content"] = "截断"
    elif fault == "expired-fill":
        payload["groups"][0]["items"][0]["memory"] = failed.ref.model_dump(mode="json")
    else:
        payload["policy_version"] = "other-version"
    with pytest.raises((AssertionError, ValueError)):
        assert_partial_pack(payload, (ready,), "long_term", (failed,), "test")


@pytest.mark.parametrize("fault", [None, "unavailable", "missing-route", "degraded"])
def test_f0_empty_requires_both_complete_routes_without_failure(fault):
    payload = pack()
    payload.update(
        outcome="empty",
        selected_sources=["working", "long_term"],
        groups=[],
        rendered_context="",
        tokens_used=0,
    )
    payload["coverage"]["long_term"] = "complete"
    if fault == "unavailable":
        payload["coverage"]["long_term"] = "unavailable"
    elif fault == "missing-route":
        payload["selected_sources"] = ["working"]
        payload["coverage"]["long_term"] = "not_requested"
    elif fault == "degraded":
        payload["degradation_reasons"] = ["long_term_dependency"]
    if fault is None:
        assert_both_empty(payload, "test")
    else:
        with pytest.raises((AssertionError, ValueError)):
            assert_both_empty(payload, "test")
