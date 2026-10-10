"""Ensure AET-42 observation delegates and rejects fallback calls."""

from types import SimpleNamespace

import pytest

from recall_all_vectors_support import assert_no_fallback, observe_discovery


@pytest.mark.parametrize(
    "method",
    ["working", "load", "history", "load_bodies", "query", "query_iterator", "hybrid_search"],
)
def test_fallback_call_cannot_be_hidden(method):
    with pytest.raises(AssertionError, match="another candidate"):
        assert_no_fallback([{"method": method, "operation_id": "failed"}], "failed")


def test_other_operation_body_control_is_not_a_fallback():
    assert_no_fallback([{"method": "load_bodies", "operation_id": "body-control"}], "failed")


@pytest.mark.asyncio
async def test_observers_delegate_sync_and_async_and_restore(monkeypatch):
    ctx = SimpleNamespace(operation_id="failed")
    delegated = []

    def load(context, refs):
        delegated.append((context, refs))
        return "real-load"

    async def bodies(context, refs):
        delegated.append((context, refs))
        return "real-body"

    provider = SimpleNamespace(load=load, load_bodies=bodies)
    runtime = SimpleNamespace(
        recall=SimpleNamespace(memories=provider, assembly=SimpleNamespace(bodies=provider)),
        vectors=SimpleNamespace(client=SimpleNamespace()),
    )
    with monkeypatch.context() as patch:
        calls = observe_discovery(runtime, patch)
        assert provider.load(ctx, ("ref",)) == "real-load"
        assert await provider.load_bodies(ctx, ("ref",)) == "real-body"
        assert calls == [
            {"method": "load", "operation_id": "failed"},
            {"method": "load_bodies", "operation_id": "failed"},
        ]
    assert delegated == [(ctx, ("ref",)), (ctx, ("ref",))]
    assert provider.load is load and provider.load_bodies is bodies
