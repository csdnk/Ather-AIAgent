"""First-batch acceptance against test doubles, not production RF evidence."""

import asyncio
import json
from datetime import timedelta
from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError
from tests.unit.recall.helpers import Authority, policy, recall_input, scope

from aether_agent_memory.recall.admission import (
    RecallAdmissionService,
    RecallError,
    RecallFilters,
    RecallInput,
)
from aether_agent_memory.recall.execution import (
    RecallExecutionService,
    guard_for,
    validate_transition,
)
from aether_agent_memory.recall.models import RecallCheckpoint
from aether_agent_memory.recall.ports import (
    RecallAdmissionUnconfirmedError,
    RecallWriteUnknownError,
)
from aether_agent_memory.recall.store import RecordRecallExecutionStore
from aether_agent_memory.runtime.contract_types import Stage, TerminalState, hash_json, utcnow
from recall_records import InMemoryRecallRecords


def setup(*, authority=None, settings=None, store=None, gate=None):
    records = InMemoryRecallRecords()
    store = store or RecordRecallExecutionStore(records)
    service = RecallAdmissionService(
        None,
        authority or Authority(),
        settings or policy(),
        execution_store=store,
        admission_gate=gate,
    )
    return service, store, records


def checkpoint(admitted, *, output=None, **changes):
    # Fixture evidence only; a real stage must supply actual verified output.
    output = {"evidence": "fixture-authority"} if output is None else output
    req = admitted.request
    cp = RecallCheckpoint(
        schema_version="recall-data-0.2",
        tenant_id=req.tenant_id,
        created_at=utcnow(),
        recall_id=req.recall_id,
        request_id=req.request_id,
        trace_id=req.trace_id,
        checkpoint_ref=uuid4().hex,
        stage="RUNNING_REQUEST_VALIDATION",
        input_digest=req.request_fingerprint,
        output_ref=uuid4().hex,
        output_digest=hash_json(output),
        completed_at=utcnow(),
        source_schema_versions={"request": req.schema_version},
        policy_version=req.policy_version,
        attempt=1,
        sensitivity="restricted_metadata",
    ).replaced(**changes)
    return cp, json.dumps(output)


@pytest.mark.parametrize(
    "current,working,long_term,types,mode",
    [
        (True, True, True, None, "combined"),
        (True, True, False, None, "working_only"),
        (False, None, True, None, "long_term_only"),
        (True, True, True, ["Working"], "working_only"),
        (True, True, True, ["Semantic"], "long_term_only"),
    ],
)
async def test_three_modes_without_database(current, working, long_term, types, mode):
    authority = Authority()
    authority.working, authority.long_term = working, long_term
    service, store, _ = setup(authority=authority)
    admitted = await service.admit(
        recall_input(
            scope=scope(session_id="s" if current else None),
            retrieval_constraints=RecallFilters(memory_types=types),
        )
    )
    assert admitted.request.retrieval_mode == mode
    execution = RecallExecutionService(store).start("tenant", admitted.request.recall_id, owner="w")
    assert execution.state == "RUNNING_REQUEST_VALIDATION"
    assert execution.result_ref is None
    assert execution.checkpoint_refs == {}
    assert execution.query_embedding_call is None


async def test_task_only_working_scope_is_supported():
    service, _, _ = setup()
    result = await service.admit(recall_input(scope=scope(session_id=None, task_id="task")))
    assert result.request.retrieval_mode == "combined"


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"valid": False}, "SCOPE_DENIED"),
        ({"valid": None}, "AUTHORITY_UNAVAILABLE"),
        ({"long_term": None}, "AUTHORITY_UNAVAILABLE"),
        ({"working": False, "long_term": False}, "SCOPE_DENIED"),
    ],
)
async def test_authority_rejection_has_no_records(changes, code):
    auth = Authority()
    for key, value in changes.items():
        setattr(auth, key, value)
    service, _, records = setup(authority=auth)
    with pytest.raises(RecallError, match=code):
        await service.admit(recall_input())
    with records.transaction() as tx:
        assert not tx.scan("recall-admission")
        assert not tx.scan("recall-locator")


async def test_foreign_scope_and_principal_cannot_be_discarded():
    class ForeignAuthority(Authority):
        async def authorize(self, request):
            result = await super().authorize(request)
            return result.replaced(scope=scope(session_id="other"))

    service, _, _ = setup(authority=ForeignAuthority())
    with pytest.raises(RecallError, match="SCOPE_DENIED"):
        await service.admit(recall_input())


@pytest.mark.parametrize(
    "raw_filter",
    [
        {"memory_types": []},
        {"memory_types": ["unknown"]},
        {"allowed_sources": ["working"]},
        {"occurred_after": "2026-09-16T00:00:00Z", "occurred_before": "2026-09-15T00:00:00Z"},
    ],
)
def test_invalid_filters(raw_filter):
    with pytest.raises(ValidationError):
        RecallFilters.model_validate(raw_filter)


async def test_invalid_input_is_rejected_before_authority():
    class NeverCalled:
        async def authorize(self, request):
            pytest.fail("invalid Query should not reach authority")

    service, _, _ = setup(authority=NeverCalled())
    with pytest.raises(RecallError, match="REQUEST_INVALID"):
        await service.admit(recall_input(query="   "))


async def test_server_defaults_remain_bound_after_policy_changes():
    service, store, _ = setup()
    raw = recall_input(tokenizer_id=None, tokenizer_version=None, template_version=None)
    first = await service.admit(raw)
    changed, _, _ = setup(
        store=store,
        settings=policy(
            tokenizer_id="new",
            tokenizer_version="2",
            template_version="2",
            policy_version="2",
            retrieval_space_ref="new-space",
            max_context_tokens=1,
        ),
    )
    retry = await changed.admit(raw.replaced(trace_id="new-trace"))
    assert retry == first
    for field, value in (("query", "other"), ("token_budget", 129)):
        with pytest.raises(RecallError, match="IDEMPOTENCY_CONFLICT"):
            await changed.admit(raw.replaced(**{field: value}))
    with pytest.raises(RecallError, match="IDEMPOTENCY_CONFLICT"):
        await changed.admit(raw.replaced(tokenizer_id="explicit-new-choice"))


async def test_caller_scope_and_key_isolation():
    service, _, _ = setup()
    raw = recall_input()
    results = [
        await service.admit(r)
        for r in (
            raw,
            raw.replaced(principal_ref="other"),
            raw.replaced(scope=scope(project_id="other")),
            raw.replaced(scope=scope(tenant_id="other")),
        )
    ]
    assert len({r.request.recall_id for r in results}) == 4


async def test_atomic_capacity_and_single_reservation():
    class CountingGate:
        calls = 0

        def check(self, snapshot, settings):
            self.calls += 1
            if snapshot.active_executions:
                raise RecallError("ADMISSION_BUSY")

    gate = CountingGate()
    service, _, records = setup(gate=gate)
    raw = recall_input()
    results = await asyncio.gather(*(service.admit(raw) for _ in range(12)))
    assert len({r.request.recall_id for r in results}) == 1
    assert gate.calls == 1
    with pytest.raises(RecallError, match="ADMISSION_BUSY"):
        await service.admit(raw.replaced(idempotency_key="other"))
    with records.transaction() as tx:
        assert len(tx.scan("recall-admission")) == 1


class LostReplyStore(RecordRecallExecutionStore):
    writes = 0

    def admit(self, *args):
        self.writes += 1
        super().admit(*args)
        raise RecallWriteUnknownError("response lost")


async def test_lost_admission_reply_queries_same_binding():
    store = LostReplyStore(InMemoryRecallRecords())
    service, _, _ = setup(store=store)
    raw = recall_input()
    first = await service.admit(raw)
    assert await service.admit(raw) == first
    assert store.writes == 1


async def test_unknown_write_then_absent_does_not_reissue():
    class UnresolvedStore(LostReplyStore):
        def admit(self, *args):
            self.writes += 1
            raise RecallWriteUnknownError("delayed, not known to be rolled back")

    store = UnresolvedStore(InMemoryRecallRecords())
    service, _, _ = setup(store=store)
    raw = recall_input()
    for _ in range(2):
        with pytest.raises(RecallAdmissionUnconfirmedError):
            await service.admit(raw)
    assert store.writes == 1


async def test_read_unknown_never_starts_a_write():
    class PendingStore(LostReplyStore):
        def find(self, *args):
            raise RecallWriteUnknownError("RF durable pending intent")

    store = PendingStore(InMemoryRecallRecords())
    service, _, _ = setup(store=store)
    with pytest.raises(RecallAdmissionUnconfirmedError):
        await service.admit(recall_input())
    assert store.writes == 0


async def test_atomic_failure_rolls_back_both_initial_records():
    class RejectGate:
        def check(self, snapshot, settings):
            raise RecallError("ADMISSION_BUSY")

    service, _, records = setup(gate=RejectGate())
    with pytest.raises(RecallError, match="ADMISSION_BUSY"):
        await service.admit(recall_input())
    with records.transaction() as tx:
        assert not tx.scan("recall-admission")
        assert not tx.scan("recall-locator")


async def test_query_entry_requires_saved_bound_checkpoint_and_new_cas():
    service, store, records = setup()
    admitted = await service.admit(recall_input())
    execution = RecallExecutionService(store).start("tenant", admitted.request.recall_id, owner="w")
    guard = guard_for(execution)
    with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
        store.advance(guard, "RUNNING_QUERY_EMBEDDING")
    cp, output = checkpoint(admitted)
    execution = store.save_checkpoint(guard, cp, output)
    with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
        store.advance(guard, "RUNNING_QUERY_EMBEDDING")
    execution = store.advance(guard_for(execution), "RUNNING_QUERY_EMBEDDING")
    assert execution.result_ref is None
    assert execution.execution_deadline_at == admitted.request.deadline_at
    with records.transaction() as tx:
        assert tx.get("recall-output", "tenant", cp.output_ref) == output


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "foreign"},
        {"recall_id": "foreign"},
        {"policy_version": "other"},
        {"stage": "RUNNING_QUERY_EMBEDDING"},
        {"attempt": 0},
    ],
)
async def test_checkpoint_binding_rejects_foreign_or_wrong_stage(changes):
    service, store, records = setup()
    admitted = await service.admit(recall_input())
    execution = RecallExecutionService(store).start("tenant", admitted.request.recall_id, owner="w")
    cp, output = checkpoint(admitted, **changes)
    with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
        store.save_checkpoint(guard_for(execution), cp, output)
    with records.transaction() as tx:
        assert not tx.scan("recall-checkpoint")


async def test_modified_checkpoint_payload_cannot_advance():
    service, store, records = setup()
    admitted = await service.admit(recall_input())
    execution = RecallExecutionService(store).start("tenant", admitted.request.recall_id, owner="w")
    cp, output = checkpoint(admitted)
    execution = store.save_checkpoint(guard_for(execution), cp, output)
    with records.transaction() as tx:
        tx.put("recall-output", "tenant", cp.output_ref, "{}")
    with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
        store.advance(guard_for(execution), "RUNNING_QUERY_EMBEDDING")


async def test_expired_lease_and_stale_worker_are_rejected():
    now = utcnow()
    store = RecordRecallExecutionStore(InMemoryRecallRecords(), clock=lambda: now)
    service, _, _ = setup(store=store)
    admitted = await service.admit(recall_input())
    now = admitted.request.created_at
    first = store.claim(
        "tenant", admitted.request.recall_id, 0, "w1", now + timedelta(milliseconds=50)
    )
    with pytest.raises(RecallError, match="ADMISSION_BUSY"):
        store.claim(
            "tenant",
            admitted.request.recall_id,
            first.state_version,
            "w2",
            now + timedelta(seconds=1),
        )
    now += timedelta(milliseconds=100)
    with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
        store.advance(guard_for(first), "RUNNING_REQUEST_VALIDATION")
    second = store.claim(
        "tenant", admitted.request.recall_id, first.state_version, "w2", now + timedelta(seconds=1)
    )
    assert first.lease_token != second.lease_token
    with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
        store.advance(guard_for(first), "RUNNING_REQUEST_VALIDATION")
    now = admitted.request.deadline_at
    with pytest.raises(RecallError, match="DEADLINE_EXCEEDED"):
        store.advance(guard_for(second), "RUNNING_REQUEST_VALIDATION")


def test_thirteen_states_and_no_terminal_rollback():
    states = ["CREATED", *get_args(Stage), *get_args(TerminalState)]
    assert len(states) == 13
    for terminal in get_args(TerminalState):
        for target in states:
            with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
                validate_transition(terminal, target)
    with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
        validate_transition("CREATED", "RUNNING_QUERY_EMBEDDING")


async def test_terminal_and_cleared_records_do_not_replay_body():
    service, store, records = setup()
    raw = recall_input()
    admitted = await service.admit(raw)
    cleared = admitted.replaced(
        execution=admitted.execution.replaced(
            retention=admitted.execution.retention.replaced(
                payload_state="cleared", payload_cleared_at=utcnow()
            ),
        )
    )
    with records.transaction() as tx:
        tx.put("recall-admission", "tenant", service.request_key(raw), cleared.model_dump_json())
    with pytest.raises(RecallError, match="REPLAY_REVALIDATION_REQUIRED"):
        await service.admit(raw)
    assert (
        store.get("tenant", admitted.request.recall_id).request.recall_id
        == admitted.request.recall_id
    )


async def test_cancelled_authority_waiter_does_not_cancel_other_caller():
    started = asyncio.Event()
    release = asyncio.Event()

    class WaitingAuthority(Authority):
        async def authorize(self, request):
            if request.trace_id == "cancel-me":
                started.set()
                await release.wait()
            return await super().authorize(request)

    service, store, _ = setup(authority=WaitingAuthority())
    raw = recall_input()
    waiting = asyncio.create_task(service.admit(raw.replaced(trace_id="cancel-me")))
    await started.wait()
    admitted = await service.admit(raw)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert await service.admit(raw) == admitted
    assert store.get("tenant", admitted.request.recall_id) == admitted


def test_client_cannot_supply_source_selection():
    payload = recall_input().model_dump()
    payload["source_selection"] = {"working_eligibility": "eligible"}
    with pytest.raises(ValidationError):
        RecallInput.model_validate(payload)


async def test_no_type_eligible_source_is_invalid_not_empty():
    authority = Authority()
    authority.long_term = False
    service, _, records = setup(authority=authority)
    with pytest.raises(RecallError, match="REQUEST_INVALID"):
        await service.admit(
            recall_input(
                retrieval_constraints=RecallFilters(memory_types=["Semantic"]),
            )
        )
    with records.transaction() as tx:
        assert not tx.scan("recall-admission")


async def test_source_revocation_rejects_but_new_authority_does_not_expand():
    authority = Authority()
    authority.working = False
    service, _, _ = setup(authority=authority)
    raw = recall_input()
    first = await service.admit(raw)
    authority.working = True
    assert (await service.admit(raw)).request.retrieval_mode == "long_term_only"
    authority.long_term = False
    with pytest.raises(RecallError, match="SCOPE_DENIED"):
        await service.admit(raw)
    assert first.execution.execution_deadline_at == first.request.deadline_at


async def test_authority_service_errors_fail_closed():
    class Unavailable:
        async def authorize(self, request):
            raise ConnectionError("fixture unavailable")

    service, _, _ = setup(authority=Unavailable())
    with pytest.raises(RecallError, match="AUTHORITY_UNAVAILABLE"):
        await service.admit(recall_input())


async def test_queued_execution_cannot_exceed_worker_capacity():
    service, store, _ = setup(settings=policy(max_inflight_requests=1, max_queued_requests=1))
    first = await service.admit(recall_input())
    second = await service.admit(recall_input(idempotency_key="second"))
    RecallExecutionService(store).start("tenant", first.request.recall_id, owner="w1")
    with pytest.raises(RecallError, match="ADMISSION_BUSY"):
        RecallExecutionService(store).start("tenant", second.request.recall_id, owner="w2")
    assert store.get("tenant", second.request.recall_id).execution.state == "CREATED"


async def test_recheck_execution_after_authority_await():
    service, store, records = setup()
    raw = recall_input()
    admitted = await service.admit(raw)

    class ClearingAuthority(Authority):
        async def authorize(self, request):
            cleared = admitted.replaced(
                execution=admitted.execution.replaced(
                    retention=admitted.execution.retention.replaced(
                        payload_state="cleared",
                        payload_cleared_at=utcnow(),
                    ),
                )
            )
            with records.transaction() as tx:
                tx.put(
                    "recall-admission",
                    "tenant",
                    service.request_key(raw),
                    cleared.model_dump_json(),
                )
            return await super().authorize(request)

    retry_service, _, _ = setup(store=store, authority=ClearingAuthority())
    with pytest.raises(RecallError, match="REPLAY_REVALIDATION_REQUIRED"):
        await retry_service.admit(raw)


async def test_atomic_racing_policies_bind_first_server_defaults():
    reached = asyncio.Event()
    released = asyncio.Event()

    class SlowAuthority(Authority):
        async def authorize(self, request):
            reached.set()
            await released.wait()
            return await super().authorize(request)

    store = RecordRecallExecutionStore(InMemoryRecallRecords())
    slow, _, _ = setup(store=store, authority=SlowAuthority())
    winner, _, _ = setup(
        store=store,
        settings=policy(
            policy_version="winner",
            tokenizer_id="winner",
            retrieval_space_ref="winner",
        ),
    )
    raw = recall_input(tokenizer_id=None, tokenizer_version=None, template_version=None)
    loser_task = asyncio.create_task(slow.admit(raw))
    await reached.wait()
    first = await winner.admit(raw)
    released.set()
    assert await loser_task == first


async def test_expired_execution_is_not_restarted_by_fresh_request_deadline():
    service, _, records = setup()
    raw = recall_input()
    admitted = await service.admit(raw)
    past = utcnow() - timedelta(seconds=1)
    expired = admitted.replaced(
        request=admitted.request.replaced(deadline_at=past),
        execution=admitted.execution.replaced(execution_deadline_at=past),
    )
    with records.transaction() as tx:
        tx.put("recall-admission", "tenant", service.request_key(raw), expired.model_dump_json())
    with pytest.raises(RecallError, match="DEADLINE_EXCEEDED"):
        await service.admit(raw.replaced(deadline_at=utcnow() + timedelta(seconds=10)))
    with records.transaction() as tx:
        assert len(tx.scan("recall-admission")) == 1


async def test_terminal_transition_requires_separate_finalization_capability():
    service, store, records = setup()
    raw = recall_input()
    admitted = await service.admit(raw)
    execution = RecallExecutionService(store).start("tenant", admitted.request.recall_id, owner="w")
    staged = admitted.replaced(execution=execution.replaced(state="RUNNING_TRACE_FINALIZATION"))
    with records.transaction() as tx:
        tx.put("recall-admission", "tenant", service.request_key(raw), staged.model_dump_json())
    with pytest.raises(RecallError, match="INVARIANT_VIOLATION"):
        store.advance(guard_for(staged.execution), "COMPLETE_AVAILABLE")
