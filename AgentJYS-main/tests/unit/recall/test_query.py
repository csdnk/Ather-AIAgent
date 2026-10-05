import json

import pytest
from tests.unit.recall.helpers import (
    Authority,
    Backend,
    binding,
    policy,
    recall_input,
    runtime,
    scope,
)

from aether_agent_memory.recall.admission import RecallAdmissionService, RecallError, RecallFilters
from aether_agent_memory.recall.embedding.service import SemanticEmbeddingService
from aether_agent_memory.recall.embedding_input import RecallQueryInputAdapter
from aether_agent_memory.recall.models import RecallCheckpoint
from aether_agent_memory.recall.query import RecallQueryService
from aether_agent_memory.runtime.contract_types import hash_json
from azure_test_runtime import AzureRecords


@pytest.mark.parametrize(
    "types,current_scope,working_read,long_term_read,expected",
    [
        (None, True, True, True, ["Working", "Episodic", "Semantic"]),
        (["Working"], True, True, True, ["Working"]),
        (None, False, True, True, ["Episodic", "Semantic"]),
        (None, True, False, True, ["Episodic", "Semantic"]),
        (None, True, True, False, ["Working"]),
        (["Working", "Semantic"], True, False, True, ["Semantic"]),
        (["Episodic"], True, True, True, ["Episodic"]),
    ],
)
async def test_each_source_prepares_vector_search_without_context_claim(
    tmp_path, types, current_scope, working_read, long_term_read, expected
):
    store = AzureRecords(tmp_path / "state.db")
    authority = Authority()
    authority.working, authority.long_term = working_read, long_term_read
    admission = RecallAdmissionService(store, authority, policy())
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
        scope=scope(session_id="session" if current_scope else None),
        retrieval_constraints=RecallFilters(memory_types=types) if types else None,
    )
    first = await service.prepare(raw)
    replay = await service.prepare(raw)
    assert replay == first
    assert q.calls == 1
    assert first.search_input.query_vector == [1, 2, 3]
    assert first.search_input.memory_types == expected
    assert first.query_result.usage == "Query"
    with store.transaction() as tx:
        records = tx.scan("recall-admission")
        execution = json.loads(records[0][2])["execution"]
        assert execution["state"] == "RUNNING_VECTOR_SEARCH"
        assert execution["result_ref"] is None
        assert len(execution["checkpoint_refs"]) == 2
        assert len(execution["read_ledger"]["attempts"]) == 1
        assert tx.scan("projection") == []
    await embedding.close()
    store.close()


async def test_replay_rejects_legacy_prepared_query_with_unauthorized_types(tmp_path):
    store = AzureRecords(tmp_path / "state.db")
    authority = Authority()
    authority.working = False
    admission = RecallAdmissionService(store, authority, policy())
    embedding = SemanticEmbeddingService(
        store,
        RecallQueryInputAdapter(admission, caller_ref="recall-A"),
        runtime(Backend()),
        runtime(Backend()),
    )
    service = RecallQueryService(
        admission,
        embedding,
        binding(),
        caller_ref="recall-A",
        execution_policy_ref="embedding-policy-0.1",
    )
    raw = recall_input()
    try:
        prepared = await service.prepare(raw)
        legacy = prepared.model_copy(
            update={
                "search_input": prepared.search_input.model_copy(
                    update={"memory_types": ["Working", "Episodic", "Semantic"]}
                )
            }
        )
        with store.transaction() as tx:
            tenant = raw.scope.tenant_id
            key = tx.get("recall-locator", tenant, prepared.recall_id)
            saved = json.loads(tx.get("recall-admission", tenant, key))
            checkpoint_ref = saved["execution"]["checkpoint_refs"]["RUNNING_QUERY_EMBEDDING"]
            checkpoint = RecallCheckpoint.model_validate_json(
                tx.get("recall-checkpoint", tenant, checkpoint_ref)
            ).replaced(output_digest=hash_json(legacy.model_dump(mode="json")))
            tx.put("recall-prepared-query", tenant, prepared.recall_id, legacy.model_dump_json())
            tx.put("recall-output", tenant, checkpoint.output_ref, legacy.model_dump_json())
            tx.put("recall-checkpoint", tenant, checkpoint_ref, checkpoint.model_dump_json())
        with pytest.raises(RecallError) as failure:
            await service.prepare(raw)
        assert failure.value.code == "REPLAY_REVALIDATION_REQUIRED"
    finally:
        await embedding.close()
        store.close()
