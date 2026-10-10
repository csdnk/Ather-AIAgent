"""AET-40 source-local SDK faults and exact partial/empty assertions."""

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

from aether_agent_memory.recall.contracts.models import ContextPack
from recall_both_sources_support import SOURCES
from recall_working_search_support import assert_execution


class SourceFault:
    """Inject only the requested route/session; other SDK calls always delegate.

    TimeoutError models an SDK transport timeout. It does not shorten the overall
    Recall deadline or fabricate a source result. Context survives the SDK thread.
    """

    def __init__(self, runtime, session, source, kind):
        assert source in SOURCES and kind in {"timeout", "unavailable"}
        self.runtime, self.session, self.source, self.kind = runtime, session, source, kind
        self.attempts = []

    @contextmanager
    def installed(self, monkeypatch):
        vectors = self.runtime.vectors
        search, sdk = vectors.search, vectors.client.search
        active = ContextVar("aet40_source_fault", default=None)

        async def route(ctx, request):
            token = active.set((ctx, request))
            try:
                return await search(ctx, request)
            finally:
                active.reset(token)

        def sdk_search(*args, **kwargs):
            current = active.get()
            if current is not None:
                ctx, request = current
                if (
                    request.memory_source == self.source
                    and request.selection.session_id == self.session
                ):
                    self.attempts.append(
                        {
                            "operation_id": ctx.operation_id,
                            "trace_id": ctx.trace_id,
                            "source": self.source,
                            "kind": self.kind,
                            "stage": "milvus.search",
                            "filter": kwargs["filter"],
                            "collection": kwargs["collection_name"],
                        }
                    )
                    if self.kind == "timeout":
                        raise TimeoutError("AET-40 owned source SDK timeout")
                    raise OSError("AET-40 owned source unavailable")
            return sdk(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(vectors, "search", route)
            patch.setattr(vectors.client, "search", sdk_search)
            yield self


def assert_partial_pack(payload, healthy, failed, forbidden, policy_version):
    pack = ContextPack.model_validate(payload)
    assert pack.selected_sources == SOURCES and pack.outcome == "degraded"
    assert healthy and pack.scope == healthy[0].ref.scope
    other = "long_term" if failed == "working" else "working"
    assert getattr(pack.coverage, other) == "complete"
    assert getattr(pack.coverage, failed) == "unavailable"
    assert failed + "_dependency" in pack.degradation_reasons
    assert pack.policy_version == policy_version
    items = [item for group in pack.groups for item in group.items]
    assert Counter(item.memory for item in items) == Counter(m.ref for m in healthy)
    for item in items:
        memory = next(m for m in healthy if m.ref == item.memory)
        assert item.content == memory.content and item.sources == memory.sources
        assert item.representation == "original" and item.content in pack.rendered_context
    for memory in forbidden:
        assert all(item.memory != memory.ref for item in items)
        if memory.content not in {m.content for m in healthy}:
            assert memory.content not in pack.rendered_context
    return pack


def assert_both_empty(payload, policy_version):
    pack = ContextPack.model_validate(payload)
    assert pack.selected_sources == SOURCES
    assert pack.coverage.working == pack.coverage.long_term == "complete"
    assert pack.outcome == "empty" and not pack.degradation_reasons
    assert not pack.groups and pack.rendered_context == "" and pack.tokens_used == 0
    assert pack.policy_version == policy_version
    return pack


def assert_fault_execution(
    probe, fault, operation_id, query, native, expected_refs, allowed_discovery_refs=()
):
    rows = [r for r in probe.searches if r["operation_id"] == operation_id]
    assert {r["request"].memory_source for r in rows} == set(SOURCES), "both routes must execute"
    attempts = [a for a in fault.attempts if a["operation_id"] == operation_id]
    failed = [r for r in rows if r["request"].memory_source == fault.source]
    assert failed and all(r["result"] is None for r in failed)
    assert attempts and all(a["source"] == fault.source for a in attempts)
    other = "long_term" if fault.source == "working" else "working"
    healthy = SimpleNamespace(
        embeddings=probe.embeddings,
        searches=[r for r in rows if r["request"].memory_source == other],
    )
    observed = assert_execution(
        healthy, operation_id, query, native, other, expected_refs, allowed_discovery_refs
    )
    assert all(a["trace_id"] == observed["trace_id"] for a in attempts)
    return {
        "selected_sources": list(SOURCES),
        "healthy": observed,
        "failed_source": fault.source,
        "failed_calls": len(failed),
        "faults": attempts,
    }
