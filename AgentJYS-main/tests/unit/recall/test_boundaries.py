import asyncio
import json
from datetime import timedelta

import pytest
from tests.unit.recall.helpers import (
    Authority,
    Backend,
    Inputs,
    binding,
    policy,
    recall_input,
    runtime,
)

from aether_agent_memory.recall.admission import RecallAdmissionService, RecallError
from aether_agent_memory.recall.embedding.models import SemanticEmbeddingRequest
from aether_agent_memory.recall.embedding.service import (
    SemanticEmbeddingError,
    SemanticEmbeddingService,
)
from aether_agent_memory.recall.embedding_input import RecallQueryInputAdapter
from aether_agent_memory.recall.query import RecallQueryService
from aether_agent_memory.runtime.contract_types import utcnow
from azure_test_runtime import AzureRecords


async def test_authority_time_is_part_of_original_budget(tmp_path, monkeypatch):
    import aether_agent_memory.recall.admission as module

    start = utcnow()
    now = start

    class SlowAuthority(Authority):
        async def authorize(self, request):
            nonlocal now
            now = start + timedelta(milliseconds=1200)
            return await super().authorize(request)

    monkeypatch.setattr(module, "utcnow", lambda: now)
    store = AzureRecords(tmp_path / "state.db")
    admitted = await RecallAdmissionService(store, SlowAuthority(), policy()).admit(recall_input())
    assert admitted.request.deadline_at == start + timedelta(milliseconds=2000)
    store.close()


async def test_authority_adapter_exception_has_stable_error(tmp_path):
    class BrokenAuthority(Authority):
        async def authorize(self, request):
            raise OSError("unreachable")

    store = AzureRecords(tmp_path / "state.db")
    with pytest.raises(RecallError, match="AUTHORITY_UNAVAILABLE"):
        await RecallAdmissionService(store, BrokenAuthority(), policy()).admit(recall_input())
    with store.transaction() as tx:
        assert tx.scan("recall-admission") == []
    store.close()


async def test_separate_workers_share_one_durable_inference(tmp_path):
    path = tmp_path / "state.db"
    stores = [AzureRecords(path), AzureRecords(path)]
    inputs = Inputs()
    gate = asyncio.Event()
    backends = [Backend(gate=gate), Backend()]
    services = [
        SemanticEmbeddingService(s, inputs, runtime(b), runtime(Backend()))
        for s, b in zip(stores, backends, strict=True)
    ]
    first = asyncio.create_task(services[0].embed(inputs.request()))
    await backends[0].started.wait()
    second = asyncio.create_task(services[1].embed(inputs.request()))
    await asyncio.sleep(0.03)
    assert backends[1].calls == 0
    gate.set()
    assert await first == await second
    assert backends[0].calls == 1
    for svc, store in zip(services, stores, strict=True):
        await svc.close()
        store.close()


async def test_late_backend_cannot_publish_success(tmp_path):
    class LateBackend(Backend):
        async def compute(self, request, text):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                # A provider may return despite the local cancellation request.
                return await super().compute(request, text)

    store = AzureRecords(tmp_path / "state.db")
    inputs = Inputs()
    svc = SemanticEmbeddingService(store, inputs, runtime(LateBackend()), runtime(Backend()))
    with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_DEADLINE_EXCEEDED"):
        await svc.embed(inputs.request(deadline_at=utcnow() + timedelta(milliseconds=80)))
    await asyncio.sleep(0.03)
    with store.transaction() as tx:
        assert tx.scan("semantic-result") == []
        record = json.loads(tx.scan("semantic-execution")[0][2])
        assert record["execution"]["state"] == "failed"
    await svc.close()
    store.close()


@pytest.mark.parametrize("fault", ["missing", "hash", "tokens"])
async def test_invalid_resolved_input_rejected_before_compute(tmp_path, fault):
    store = AzureRecords(tmp_path / "state.db")
    inputs = Inputs()
    request = inputs.request(text="x" * 129 if fault == "tokens" else "text")
    if fault == "missing":
        inputs.values.clear()
    if fault == "hash":
        _, digest = inputs.values[request.input_ref]
        inputs.values[request.input_ref] = ("changed", digest)
    backend = Backend()
    svc = SemanticEmbeddingService(store, inputs, runtime(backend), runtime(Backend()))
    with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_INPUT_INVALID"):
        await svc.embed(request)
    assert backend.calls == 0
    await svc.close()
    store.close()


async def test_query_adapter_reauthorizes_and_cached_checkpoint_detects_corruption(tmp_path):
    store = AzureRecords(tmp_path / "state.db")
    authority = Authority()
    admission = RecallAdmissionService(store, authority, policy())
    inputs = RecallQueryInputAdapter(admission, caller_ref="recall-A")
    embedding = SemanticEmbeddingService(store, inputs, runtime(Backend()), runtime(Backend()))
    svc = RecallQueryService(
        admission,
        embedding,
        binding(),
        caller_ref="recall-A",
        execution_policy_ref="embedding-policy-0.1",
    )
    raw = recall_input()
    prepared = await svc.prepare(raw)
    with store.transaction() as tx:
        saved = tx.get("recall-query-binding", "tenant", prepared.recall_id)
    semantic = SemanticEmbeddingRequest.model_validate_json(saved)
    assert (await inputs.resolve(semantic)).text == raw.query
    with pytest.raises(SemanticEmbeddingError, match="EMBEDDING_INPUT_INVALID"):
        await inputs.authorize(semantic.replaced(caller_ref="other"))
    authority.long_term = False
    with pytest.raises(RecallError, match="SCOPE_DENIED"):
        await inputs.authorize(semantic)
    authority.long_term = True
    with store.transaction() as tx:
        data = prepared.model_dump(mode="json")
        data["search_input"]["query_vector"] = [9, 9, 9]
        tx.put("recall-prepared-query", "tenant", prepared.recall_id, json.dumps(data))
    with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
        await svc.prepare(raw)
    await embedding.close()
    store.close()
