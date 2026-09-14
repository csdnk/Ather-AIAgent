import asyncio
import json

import pytest
from tests.unit.recall.helpers import Backend, Inputs, runtime

from aether_agent_memory.b1.semantic.service import (
    EmbeddingPolicy,
    SemanticEmbeddingError,
    SemanticEmbeddingService,
    TransientEmbeddingError,
)
from aether_agent_memory.runtime.capability_store import SQLiteCapabilityStore


async def test_query_passage_are_pure_and_distinct_with_completed_cache(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    inputs = Inputs()
    query = Backend()
    passage = Backend()
    svc = SemanticEmbeddingService(store, inputs, runtime(query), runtime(passage))
    request = inputs.request(text="  输入保留空白  ")
    result = await svc.embed(request)
    assert await svc.vector_for(request, result) == [1.0, 2.0, 3.0]
    replay = await svc.embed(inputs.request(text="  输入保留空白  ", caller_request_ref="new-call"))
    assert result == replay
    p = await svc.embed(
        inputs.request(text="  输入保留空白  ", usage="Passage", caller_request_ref="passage")
    )
    assert p.usage == "Passage" and p.embedding_result_id != result.embedding_result_id
    assert query.calls == passage.calls == 1
    assert query.texts == ["  输入保留空白  "]
    with store.transaction() as tx:
        assert tx.scan("projection") == []
        records = [json.loads(v) for _, _, v in tx.scan("semantic-execution")]
    assert sorted(r["execution"]["attempts_reserved"] for r in records) == [0, 1, 1]
    await svc.close()
    store.close()


async def test_cancellation_does_not_cancel_shared_inference(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    inputs = Inputs()
    gate = asyncio.Event()
    query = Backend(gate=gate)
    svc = SemanticEmbeddingService(store, inputs, runtime(query), runtime(Backend()))
    request = inputs.request()
    waiter = asyncio.create_task(svc.embed(request))
    await query.started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    next_waiter = asyncio.create_task(svc.embed(inputs.request()))
    gate.set()
    result = await next_waiter
    assert result.usage == "Query" and query.calls == 1
    await svc.close()
    store.close()


async def test_completed_result_reopens_and_reauthorizes(tmp_path):
    path = tmp_path / "state.db"
    store = SQLiteCapabilityStore(path)
    inputs = Inputs()
    q = Backend()
    svc = SemanticEmbeddingService(store, inputs, runtime(q), runtime(Backend()))
    request = inputs.request()
    first = await svc.embed(request)
    await svc.close()
    store.close()
    store = SQLiteCapabilityStore(path)
    q = Backend()
    svc = SemanticEmbeddingService(store, inputs, runtime(q), runtime(Backend()))
    assert await svc.embed(inputs.request()) == first
    assert q.calls == 0
    inputs.allowed = False
    with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_INPUT_INVALID"):
        await svc.embed(request)
    await svc.close()
    store.close()


@pytest.mark.parametrize(
    "vector", [[0.0, 0.0, 0.0], [1.0, 2.0], [float("nan"), 1, 2], [True, 1, 2], [1e50, 1, 2]]
)
async def test_invalid_vectors_never_produce_result(tmp_path, vector):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    inputs = Inputs()
    q = Backend(vector=vector)
    svc = SemanticEmbeddingService(store, inputs, runtime(q), runtime(Backend()))
    with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_COMPUTE_FAILED"):
        await svc.embed(inputs.request())
    with store.transaction() as tx:
        assert tx.scan("semantic-result") == []
    assert q.calls == 1
    await svc.close()
    store.close()


async def test_wrong_binding_and_same_caller_changed_input_are_rejected(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    inputs = Inputs()
    q = Backend(wrong_binding=True)
    svc = SemanticEmbeddingService(store, inputs, runtime(q), runtime(Backend()))
    with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_BINDING_MISMATCH"):
        await svc.embed(inputs.request())
    with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_BINDING_MISMATCH"):
        await svc.embed(inputs.request(text="changed"))
    assert q.calls == 1
    await svc.close()
    store.close()


async def test_passage_cannot_take_query_capacity(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    inputs = Inputs()
    gate = asyncio.Event()
    p = Backend(gate=gate)
    policy = EmbeddingPolicy(passage_queue=0)
    svc = SemanticEmbeddingService(store, inputs, runtime(Backend()), runtime(p), policy)
    task = asyncio.create_task(svc.embed(inputs.request(usage="Passage", caller_request_ref="p1")))
    await p.started.wait()
    with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_BUSY"):
        await svc.embed(inputs.request(usage="Passage", caller_request_ref="p2"))
    assert (await svc.embed(inputs.request())).usage == "Query"
    gate.set()
    await task
    await svc.close()
    store.close()


async def test_failed_execution_replay_does_not_reset_attempts(tmp_path):
    class Failing(Backend):
        async def compute(self, request, text):
            self.calls += 1
            raise TransientEmbeddingError()

    store = SQLiteCapabilityStore(tmp_path / "state.db")
    inputs = Inputs()
    q = Failing()
    svc = SemanticEmbeddingService(store, inputs, runtime(q), runtime(Backend()))
    for _ in range(2):
        with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_COMPUTE_FAILED"):
            await svc.embed(inputs.request())
    assert q.calls == 2
    await svc.close()
    store.close()


async def test_corrupt_cached_vector_is_not_reused_for_another_call(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    inputs = Inputs()
    q = Backend()
    svc = SemanticEmbeddingService(store, inputs, runtime(q), runtime(Backend()))
    first = await svc.embed(inputs.request())
    with store.transaction() as tx:
        value = json.loads(tx.get("semantic-result", "tenant", first.embedding_result_id))
        value["vector"] = [9, 9, 9]
        tx.put("semantic-result", "tenant", first.embedding_result_id, json.dumps(value))
    second = await svc.embed(inputs.request(caller_request_ref="second"))
    assert second.embedding_result_id != first.embedding_result_id and q.calls == 2
    await svc.close()
    store.close()
