"""AET-43 harness checks; not evidence of product timeout behavior."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from recall_source_timing_support import LateSDK, pack_hash


def test_pack_hash_ignores_json_key_order_but_detects_mutation():
    assert pack_hash({"items": ["A"], "coverage": "partial"}) == pack_hash(
        {"coverage": "partial", "items": ["A"]}
    )
    assert pack_hash({"items": ["A"]}) != pack_hash({"items": ["A", "B"]})


@pytest.mark.asyncio
async def test_transport_barrier_times_out_then_returns_real_reply_and_restores(monkeypatch):
    calls = []
    sentinel = [[{"id": "real-provider-reply"}]]

    def sdk_search():
        calls.append("sdk")
        return sentinel

    client = SimpleNamespace(search=sdk_search)

    pool = ThreadPoolExecutor(max_workers=2)

    async def search(ctx, request):
        future = pool.submit(copy_context().run, client.search)
        return await asyncio.shield(asyncio.wrap_future(future))

    vectors = SimpleNamespace(search=search, client=client, executor=pool)
    runtime = SimpleNamespace(vectors=vectors)
    ctx = SimpleNamespace(operation_id="op")
    request = SimpleNamespace(
        memory_source="working",
        selection=SimpleNamespace(session_id="owned"),
        deadline_at="request-deadline",
    )
    late = LateSDK(runtime, "owned", "working")
    with late.installed(monkeypatch):
        with pytest.raises(TimeoutError):
            await vectors.search(ctx, request)
        assert late.entered.is_set() and not late.returned.is_set()
        late.stamp("commit-observed")
        late.release.set()
        assert await asyncio.to_thread(late.returned.wait, 2)
        assert late.tracked_future and await asyncio.to_thread(late.settled.wait, 2)
    assert calls == ["sdk"]
    assert [r["stage"] for r in late.timeline] == [
        "sdk_reply_held",
        "transport_deadline_armed",
        "transport_deadline_exceeded",
        "commit-observed",
        "late_sdk_return",
        "late_future_completed",
    ]
    assert vectors.search is search and client.search is sdk_search
    assert await vectors.search(ctx, request) is sentinel
    pool.shutdown(wait=True)


@pytest.mark.asyncio
async def test_transport_barrier_does_not_delay_other_session(monkeypatch):
    client = SimpleNamespace(search=lambda: [["native"]])

    async def search(ctx, request):
        return await asyncio.to_thread(client.search)

    runtime = SimpleNamespace(vectors=SimpleNamespace(search=search, client=client))
    late = LateSDK(runtime, "owned", "working")
    request = SimpleNamespace(
        memory_source="working", selection=SimpleNamespace(session_id="other")
    )
    with late.installed(monkeypatch):
        assert await runtime.vectors.search(SimpleNamespace(), request) == [["native"]]
    assert not late.entered.is_set() and not late.timeline
