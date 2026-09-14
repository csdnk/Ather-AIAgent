import json

import pytest
from tests.unit.recall.helpers import (
    Authority,
    Backend,
    binding,
    policy,
    recall_input,
    runtime,
)

from aether_agent_memory.b1.semantic.service import SemanticEmbeddingService
from aether_agent_memory.memory.recall.admission import RecallAdmissionService, RecallFilters
from aether_agent_memory.memory.recall.embedding_input import RecallQueryInputAdapter
from aether_agent_memory.memory.recall.query import RecallQueryService
from aether_agent_memory.runtime.capability_store import SQLiteCapabilityStore


@pytest.mark.parametrize("working_only", [False, True])
async def test_prepares_vector_search_or_skips_embedding_without_context_claim(
    tmp_path, working_only
):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    admission = RecallAdmissionService(store, Authority(), policy())
    inputs = RecallQueryInputAdapter(admission, caller_ref="recall-A")
    q = Backend()
    embedding = SemanticEmbeddingService(store, inputs, runtime(q), runtime(Backend()))
    service = RecallQueryService(
        admission,
        embedding,
        binding(),
        caller_ref="recall-A",
        execution_policy_ref="embedding-policy-0.1",
    )
    raw = recall_input(
        retrieval_constraints=RecallFilters(memory_types=["Working"]) if working_only else None
    )
    first = await service.prepare(raw)
    replay = await service.prepare(raw)
    assert replay == first
    assert q.calls == (0 if working_only else 1)
    if working_only:
        assert first.search_input is None and first.query_result is None
    else:
        assert first.search_input.query_vector == [1, 2, 3]
        assert first.search_input.memory_types == ["Episodic", "Semantic"]
        assert first.query_result.usage == "Query"
    with store.transaction() as tx:
        records = tx.scan("recall-admission")
        execution = json.loads(records[0][2])["execution"]
        assert execution["state"] == "RUNNING_VECTOR_SEARCH"
        assert execution["result_ref"] is None
        assert len(execution["checkpoint_refs"]) == 2
        assert len(execution["read_ledger"]["attempts"]) == (0 if working_only else 1)
        assert tx.scan("projection") == []
    await embedding.close()
    store.close()
