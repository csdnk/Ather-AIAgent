import asyncio
from datetime import timedelta

import pytest
from pydantic import ValidationError
from tests.unit.recall.helpers import Authority, policy, recall_input, scope

from aether_agent_memory.recall.admission import (
    RecallAdmissionService,
    RecallError,
    RecallFilters,
    RecallInput,
)
from aether_agent_memory.runtime.capability_store import SQLiteCapabilityStore
from aether_agent_memory.runtime.contract_types import utcnow


@pytest.mark.parametrize(
    "current,types,mode",
    [
        (True, None, "combined"),
        (False, None, "long_term_only"),
        (True, ["Working"], "working_only"),
        (True, ["Semantic"], "long_term_only"),
    ],
)
async def test_scope_union_and_initial_records(tmp_path, current, types, mode):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    raw = recall_input(
        scope=scope(session_id="session" if current else None),
        retrieval_constraints=RecallFilters(memory_types=types),
    )
    result = await RecallAdmissionService(store, Authority(), policy()).admit(raw)
    assert result.request.retrieval_mode == mode
    assert result.execution.state == "CREATED"
    assert result.execution.read_ledger.bytes_charged == 0
    assert result.execution.finalization.commit_id
    assert result.execution.result_ref is None
    assert result.request.scope.project_id is None
    assert result.request.model_dump(mode="json")["scope"]["project_id"] is None
    store.close()


@pytest.mark.parametrize("field", ["retrieval_mode", "source_selection", "allowed_sources"])
def test_clients_cannot_select_mode(field):
    payload = recall_input().model_dump()
    payload[field] = "working_only"
    with pytest.raises(ValidationError):
        RecallInput.model_validate(payload)
    with pytest.raises(ValidationError):
        RecallFilters.model_validate({"allowed_sources": ["working"]})


@pytest.mark.parametrize(
    "valid,working,long_term,code",
    [
        (True, False, False, "SCOPE_DENIED"),
        (None, True, True, "AUTHORITY_UNAVAILABLE"),
        (False, True, True, "SCOPE_DENIED"),
        (True, None, True, "AUTHORITY_UNAVAILABLE"),
    ],
)
async def test_rejection_creates_no_execution(tmp_path, valid, working, long_term, code):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    auth = Authority()
    auth.valid, auth.working, auth.long_term = valid, working, long_term
    with pytest.raises(RecallError, match=code):
        await RecallAdmissionService(store, auth, policy()).admit(recall_input())
    with store.transaction() as tx:
        assert tx.scan("recall-admission") == []
    store.close()


async def test_replay_survives_restart_and_policy_changes_but_not_revocation(tmp_path):
    path = tmp_path / "state.db"
    store = SQLiteCapabilityStore(path)
    auth = Authority()
    auth.working = False
    raw = recall_input()
    first = await RecallAdmissionService(store, auth, policy()).admit(raw)
    store.close()
    store = SQLiteCapabilityStore(path)
    auth.working = True
    newer = policy(
        policy_version="new-policy", tokenizer_id="new-tokenizer", retrieval_space_ref="new-space"
    )
    svc = RecallAdmissionService(store, auth, newer)
    replay = await svc.admit(raw.replaced(authorization_ref="fresh", trace_id="retry"))
    assert replay == first
    assert replay.request.retrieval_mode == "long_term_only"
    with pytest.raises(RecallError, match="IDEMPOTENCY_CONFLICT"):
        await svc.admit(raw.replaced(query="另一个问题"))
    auth.long_term = False
    with pytest.raises(RecallError, match="SCOPE_DENIED"):
        await svc.admit(raw)
    store.close()


async def test_concurrent_callers_reserve_once_and_busy_does_not_block_attachment(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    svc = RecallAdmissionService(
        store, Authority(), policy(max_inflight_requests=1, max_queued_requests=0)
    )
    raw = recall_input()
    results = await asyncio.gather(*(svc.admit(raw) for _ in range(8)))
    assert len({r.request.recall_id for r in results}) == 1
    assert len({r.execution.finalization.commit_id for r in results}) == 1
    with pytest.raises(RecallError, match="ADMISSION_BUSY"):
        await svc.admit(raw.replaced(idempotency_key="second"))
    store.close()


async def test_types_and_missing_model_space_do_not_silently_reduce_sources(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "state.db")
    auth = Authority()
    auth.long_term = False
    with pytest.raises(RecallError, match="REQUEST_INVALID"):
        await RecallAdmissionService(store, auth, policy()).admit(
            recall_input(retrieval_constraints=RecallFilters(memory_types=["Semantic"]))
        )
    with pytest.raises(RecallError, match="EMBEDDING_CONTRACT_MISMATCH"):
        await RecallAdmissionService(store, Authority(), policy(retrieval_space_ref=None)).admit(
            recall_input()
        )
    with pytest.raises(RecallError, match="DEADLINE_EXCEEDED"):
        await RecallAdmissionService(store, Authority(), policy()).admit(
            recall_input(deadline_at=utcnow() - timedelta(seconds=1))
        )
    store.close()
