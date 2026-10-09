"""Remember result atomicity with real task admission and isolated metadata storage."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from aether_agent_memory.remember.basic.official_commit import TWO_STAGE_COMMIT_SCHEMA
from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.basic.projection_dispatch import (
    dispatch_projections,
    dispatch_status,
    stage_projection,
)
from aether_agent_memory.remember.basic.service import Remember, memory_ref
from aether_agent_memory.remember.contracts.foundation import MemoryRecord
from aether_agent_memory.remember.contracts.models import (
    MemoryRef,
    MemorySnapshot,
    MemoryStatus,
    SourceRef,
)
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Permission,
    Principal,
    RecordRef,
    Scope,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import later, now
from aether_agent_memory.runtime.foundation.events import Events
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.tasks import Tasks
from aether_agent_memory.runtime.foundation.transactions import StorageTransaction
from aether_agent_memory.runtime.temporal.events import EventAdmission
from recall_records import InMemoryRecallRecords


@pytest.fixture
def case():
    records = InMemoryRecallRecords()

    @contextmanager
    def transaction():
        with records.transaction() as raw:
            tx = StorageTransaction(raw, "projection-dispatch-test")
            try:
                yield tx
                tx.check()
                for guard in tx.before_commit:
                    guard()
            finally:
                tx.open = False

    owner = object.__new__(RememberPipeline)
    owner.uow = SimpleNamespace(transaction=transaction, backend="test-memory", telemetry=None)
    owner.identity = Identity(owner.uow)
    owner.tasks = Tasks(owner.uow, owner.identity)
    owner.tasks.class_limits["remember_index"] = 2
    owner.tasks.register("remember.project", "remember_index", owner, permission=Permission.READ)
    owner.policy = RememberPolicy()
    owner.processing_seconds = 86400
    owner.checkpoint_binding = lambda kind=None: "projection-binding"
    owner.put = lambda tx, memory: Remember.put(owner, tx, memory)
    owner.current = lambda tx, key: MemorySnapshot.model_validate(
        tx.get(RecordRef.model_validate(tx.read("remember_current", key)))
    )
    owner.duplicate_in = lambda *args: None
    owner.comparison_source_binding = lambda *args: "source-binding"
    owner.emit = lambda tx, ctx, item, change, **kwargs: tx.write(
        "test_events", item.ref.memory_id, change
    )
    owner.final_guard = lambda tx, ctx, refs, purpose: SimpleNamespace(
        items=[SimpleNamespace(decision="allowed") for ref in refs]
    )
    owner.tasks.guard = lambda *args: None

    def finish(tx, ctx, task, value):
        tx.write("test_result", task.task_id, value)
        return value

    owner.finish = finish
    scope = Scope(tenant_id="t", application_id="app", user_id="u", agent_id="a", session_id="s")
    principal = Principal(
        principal_id="u", home_scope=scope, permissions=tuple(Permission), auth_epoch=1
    )
    ctx = TrustedContext(
        principal=principal,
        request_id="req",
        operation_id="op",
        trace_id="1" * 32,
        span_id="2" * 16,
        deadline_at=later(now(), 300),
    )
    source = SourceRef(
        source_id="source", source_version=1, content_hash=text_hash("source"), locator="source"
    )
    working = MemorySnapshot(
        ref=MemoryRef(scope=scope, memory_id="working", version=1),
        revision=1,
        object_revision=1,
        kind="working",
        status="active",
        content="source",
        content_hash=source.content_hash,
        sources=(source,),
        projection_state="pending",
        created_at=now(),
    )
    with transaction() as tx:
        tx.write(
            "identities", "u", {"enabled": True, "principal": principal.model_dump(mode="json")}
        )
        owner.put(tx, working)
        tx.write("remember_pending", "working", {"state": "scheduled", "task_id": "extract"})
    return owner, ctx, working


def prepared(working, count):
    return {
        "two_stage": True,
        "official_guard": {
            "two_stage": TWO_STAGE_COMMIT_SCHEMA,
            "refs": [],
            "sources": [s.model_dump(mode="json") for s in working.sources],
            "source_binding": "source-binding",
            "versions": {},
        },
        "candidate_groups": [[f"candidate-{index}"] for index in range(count)],
        "attempt": 0,
        "space_key": "space",
        "expected_space_seq": 0,
        "virtual": {},
        "refs": [working.ref.model_dump(mode="json")],
        "candidate_count": count,
        "proposals": [
            (
                {
                    "text": f"Distinct verified fact {i}",
                    "sources": [s.model_dump(mode="json") for s in working.sources],
                    "evidence_status": "supported",
                    "kind": "semantic",
                },
                {"outcome": "create", "reason": "new_fact"},
                None,
            )
            for i in range(count)
        ],
    }


@pytest.mark.parametrize("count", [98, 99, 100, 101, 102, 105])
def test_all_results_commit_when_projection_fanout_exceeds_scope_capacity(case, count):
    owner, ctx, working = case
    result = owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, count),
    )
    assert len(result["memories"]) == count
    with owner.uow.transaction() as tx:
        assert len(tx.rows("remember_current")) == count + 1
        assert tx.read("remember_pending", "working")["state"] == "processed"
        assert len(tx.rows("tasks")) <= owner.tasks.pending_limit
        assert len(tx.rows("remember_projection_dispatch")) == count


def test_failed_semantic_commit_rolls_back_memories_and_projection_intents(case):
    owner, ctx, working = case

    def fail(tx, ctx, task, value):
        raise RuntimeError("injected commit failure")

    owner.finish = fail
    with pytest.raises(RuntimeError, match="injected commit failure"):
        owner.commit_extraction(
            ctx,
            SimpleNamespace(task_id="extract", kind="remember.extract"),
            (working,),
            prepared(working, 3),
        )
    with owner.uow.transaction() as tx:
        assert len(tx.rows("remember_current")) == 1
        assert tx.rows("remember_projection_dispatch") == []
        assert tx.rows("tasks") == []
        assert tx.read("remember_pending", "working")["state"] == "scheduled"


def occupy(owner, scope, count):
    with owner.uow.transaction() as tx:
        for index in range(count):
            task_scope = (
                scope
                if count <= owner.tasks.pending_limit
                else scope.model_copy(
                    update={"session_id": f"occupied-{index // owner.tasks.pending_limit}"}
                )
            )
            tx.write(
                "tasks",
                f"occupied-{index}",
                {
                    "record": {
                        "subject": {"scope": task_scope.model_dump(mode="json")},
                        "state": "pending",
                    }
                },
            )


def release(owner):
    with owner.uow.transaction() as tx:
        for key, row in tx.rows("tasks"):
            tx.write("tasks", key, {**row, "record": {**row["record"], "state": "succeeded"}})


@pytest.mark.parametrize("occupied", [1, 98, 99, 100])
def test_partial_capacity_resumes_without_duplicate_memories_or_admissions(case, occupied):
    owner, ctx, working = case
    occupy(owner, working.ref.scope, occupied)
    result = owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, 105),
    )
    assert len(result["memories"]) == 105
    with owner.uow.transaction() as tx:
        rows = tx.rows("remember_projection_dispatch")
        assert sum(row["state"] == "admitted" for _, row in rows) == 100 - occupied
        assert (
            dispatch_status(tx, {ref["memory_id"] for ref in result["memories"]})["pending"]
            == 5 + occupied
        )
    # Recreate the component: pending intent progress lives in metadata, not a
    # closure/queue owned by the component instance. This is not a DB crash test.
    restarted = object.__new__(RememberPipeline)
    restarted.__dict__.update(owner.__dict__)
    release(owner)
    owner.periodic()
    release(owner)
    restarted.periodic()
    assert restarted.periodic() == 0
    with owner.uow.transaction() as tx:
        rows = tx.rows("remember_projection_dispatch")
        assert all(row["state"] == "admitted" for _, row in rows)
        assert len({row["task_id"] for _, row in rows}) == 105
        assert len(tx.rows("remember_current")) == 106
        assert len(tx.rows("remember_outbox")) == 105


def test_tenant_capacity_defers_even_when_target_scope_is_empty(case):
    owner, ctx, working = case
    occupy(owner, working.ref.scope.model_copy(update={"session_id": "other-session"}), 300)
    owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, 3),
    )
    with owner.uow.transaction() as tx:
        assert len(tx.rows("remember_current")) == 4
        assert all(row["state"] == "pending" for _, row in tx.rows("remember_projection_dispatch"))
    release(owner)
    assert owner.periodic() == 3


def test_admission_abort_rolls_back_task_and_input_without_rolling_back_result(case):
    owner, ctx, working = case

    def race(tx, task):
        tx.abort(ErrorCode.CAPACITY_EXCEEDED, "simulated competing admission")

    owner.tasks.on_admitted = race
    owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, 3),
    )
    with owner.uow.transaction() as tx:
        assert len(tx.rows("remember_current")) == 4
        assert tx.rows("tasks") == []
        assert tx.rows("task_keys") == []
        assert not [
            row for _, row in tx.rows("records") if row["ref"]["object_type"] == "processing_input"
        ]
        assert any(
            row["reason"] == "CAPACITY_EXCEEDED"
            for _, row in tx.rows("remember_projection_dispatch")
        )
    owner.tasks.on_admitted = None
    assert owner.periodic() == 3


@pytest.mark.parametrize("change", ["deleted", "superseded", "archived", "version"])
def test_deferred_projection_skips_inactive_or_replaced_final_versions(case, change):
    owner, ctx, working = case
    occupy(owner, working.ref.scope, 100)
    result = owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, 1),
    )
    with owner.uow.transaction() as tx:
        fact = owner.current(tx, result["memories"][0]["memory_id"])
        changed = (
            fact.model_copy(update={"ref": fact.ref.model_copy(update={"version": 2})})
            if change == "version"
            else fact.model_copy(update={"status": MemoryStatus(change)})
        )
        owner.put(tx, changed)
    release(owner)
    assert owner.periodic() == 0
    with owner.uow.transaction() as tx:
        assert tx.rows("remember_projection_dispatch")[0][1]["state"] == "obsolete"
        assert tx.rows("remember_outbox") == []


def test_deleting_original_working_does_not_cancel_final_projection(case):
    owner, ctx, working = case
    occupy(owner, working.ref.scope, 100)
    owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, 1),
    )
    with owner.uow.transaction() as tx:
        owner.put(tx, working.model_copy(update={"status": MemoryStatus.DELETED}))
    release(owner)
    assert owner.periodic() == 1


def test_cold_body_dispatch_uses_metadata_only_and_freezes_task_input(case):
    owner, ctx, working = case
    fact = working.model_copy(
        update={"ref": working.ref.model_copy(update={"memory_id": "cold"}), "kind": "semantic"}
    )
    location = ResourceLocation(
        kind="body",
        provider_id="ceph",
        provider_instance_id="cold",
        namespace="memories",
        object_key="opaque-key",
        generation="v1",
        content_hash=fact.content_hash,
    )
    record = MemoryRecord(
        ref=fact.ref,
        revision=1,
        relations_revision=1,
        kind="semantic",
        status="active",
        body_location=location,
        body_chars=len(fact.content),
        sources=fact.sources,
        projection_state="pending",
        created_at=now(),
    )
    with owner.uow.transaction() as tx:
        physical = memory_ref(fact.ref, versioned=True)
        tx.put_if_revision(physical, record.model_dump(mode="json"), None)
        tx.write("remember_current", fact.ref.memory_id, physical.model_dump(mode="json"))
        stage_projection(owner, tx, ctx, fact, "extract")

    def forbidden(*args):
        raise AssertionError("admission tried to hydrate cold body")

    owner.current = owner.decode = forbidden
    assert dispatch_projections(owner) == 1
    with owner.uow.transaction() as tx:
        task = tx.rows("tasks")[0][1]["record"]
        assert tx.get(RecordRef.model_validate(task["input_ref"])) == record.model_dump(mode="json")


def test_revoked_principal_cannot_dispatch_deferred_results(case):
    owner, ctx, working = case
    occupy(owner, working.ref.scope, 100)
    owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, 1),
    )
    with owner.uow.transaction() as tx:
        identity = tx.read("identities", "u")
        tx.write("identities", "u", {**identity, "enabled": False})
    release(owner)
    assert owner.periodic() == 0
    with owner.uow.transaction() as tx:
        row = tx.rows("remember_projection_dispatch")[0][1]
        assert row["state"] == "blocked"
        assert row["reason"] == "FORBIDDEN"


def test_changed_provider_binding_blocks_old_intent_but_manual_reindex_recovers_status(case):
    owner, ctx, working = case
    occupy(owner, working.ref.scope, 100)
    result = owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, 1),
    )
    release(owner)
    owner.checkpoint_binding = lambda kind=None: "new-provider-binding"
    assert owner.periodic() == 0
    memory_ids = {ref["memory_id"] for ref in result["memories"]}
    with owner.uow.transaction() as tx:
        status = dispatch_status(tx, memory_ids)
        assert status["blocked"] == 1
        assert status["items"][0]["reason"] == "VERSION_CONFLICT"
        fact = owner.current(tx, result["memories"][0]["memory_id"])
        owner.enqueue(
            tx,
            ctx.model_copy(update={"operation_id": "explicit-reindex"}),
            fact,
            "remember.project",
        )
    with owner.uow.transaction() as tx:
        status = dispatch_status(tx, memory_ids)
        assert status["blocked"] == 0
        assert status["items"][0]["state"] == "admitted"
    assert owner.periodic() == 0


def test_deleted_intent_not_reported_as_unfinished_when_queue_is_still_full(case):
    owner, ctx, working = case
    occupy(owner, working.ref.scope, 100)
    result = owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, 1),
    )
    with owner.uow.transaction() as tx:
        fact = owner.current(tx, result["memories"][0]["memory_id"])
        owner.put(tx, fact.model_copy(update={"status": MemoryStatus.DELETED}))
    assert owner.periodic() == 0
    with owner.uow.transaction() as tx:
        status = dispatch_status(tx, {fact.ref.memory_id})
        assert status["pending"] == 0
        assert status["items"][0]["state"] == "obsolete"


def test_actual_event_admission_fanout_is_deferred_and_public_pending_callback_resumes(case):
    owner, ctx, working = case
    owner.events = Events(owner.uow, owner.identity)
    owner.events.register_type(
        "memory.changed", Remember.validate_event, permission=Permission.READ
    )
    for consumer in ("operate", "audit"):
        owner.events.subscribe("memory.changed", consumer, lambda *args: None)
    ledger = SimpleNamespace(tasks=owner.tasks, admit=owner.tasks.enqueue)
    EventAdmission(ledger, owner.events)
    owner.emit = lambda tx, ctx, item, change, **kwargs: Remember.emit(
        owner, tx, ctx, item, change, **kwargs
    )
    result = owner.commit_extraction(
        ctx,
        SimpleNamespace(task_id="extract", kind="remember.extract"),
        (working,),
        prepared(working, 105),
    )
    assert len(result["memories"]) == 105
    with owner.uow.transaction() as tx:
        assert len(tx.rows("remember_event_dispatch")) == 105
        assert len(tx.rows("remember_current")) == 106
        assert len(tx.active_task_rows()) <= 100
        anchor = tx.read("remember_pending", "dispatch:extract")
        assert anchor["state"] == "dispatching"
        # No original Working eligibility is needed after result publication.
        owner.put(tx, working.model_copy(update={"status": MemoryStatus.DELETED}))
        tx.write("remember_pending", "working", {"state": "obsolete"})
    for _ in range(6):
        release(owner)
        with owner.uow.transaction() as tx:
            owner.periodic_pending(tx, "dispatch:extract")
            assert len(tx.active_task_rows()) <= 100
    with owner.uow.transaction() as tx:
        assert tx.read("remember_pending", "dispatch:extract")["state"] == "processed"
        intents = tx.rows("remember_projection_dispatch")
        assert len(intents) == 105 and all(row["state"] == "admitted" for _, row in intents)
        events = tx.rows("remember_event_dispatch")
        assert len(events) == 105 and all(row["state"] == "admitted" for _, row in events)
        assert len(tx.rows("outbox")) == 105
        assert len(tx.rows("deliveries")) == 210
        for key, row in events:
            assert tx.read("outbox", key)["event"] == row["event"]
            assert row["event"]["payload"]["change_seq"] == 1
            assert row["event"]["request_id"] == ctx.request_id
