"""Working deletion is not source retraction or long-term content correction."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from aether_agent_memory.remember.basic.eligibility import qualify
from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.basic.service import Remember, memory_ref
from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    DeleteRequest,
    MemoryKind,
    MemoryRef,
    MemorySnapshot,
    SourceInput,
    SourceRef,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash


class Metadata:
    def __init__(self):
        self.data = {}
        self.scans = []

    def read(self, table, key):
        return deepcopy(self.data.get((table, key)))

    def write(self, table, key, value):
        self.data[table, key] = deepcopy(value)

    def rows(self, table):
        self.scans.append(table)
        return [(key, deepcopy(value)) for (name, key), value in self.data.items() if name == table]

    def get(self, ref):
        return self.read("records", ref.model_dump_json())

    def store(self, item):
        physical = memory_ref(item.ref, versioned=True)
        self.write("records", physical.model_dump_json(), item.model_dump(mode="json"))
        self.write("remember_current", item.ref.memory_id, physical.model_dump(mode="json"))

    def abort(self, code, message):
        raise FoundationError(code, message)

    @contextmanager
    def transaction(self):
        yield self


def fixture():
    tx = Metadata()
    scope = Scope(tenant_id="t", application_id="app", user_id="alice", agent_id="agent")
    text = "The station accepts at least 500 offline records."
    source = SourceRef(
        source_id="source", source_version=1, content_hash=text_hash(text), locator="original"
    )
    item = MemorySnapshot(
        ref=MemoryRef(scope=scope, memory_id="working", version=1),
        revision=1,
        object_revision=1,
        kind="working",
        status="active",
        content=text,
        content_hash=text_hash(text),
        sources=(source,),
        projection_state="pending",
        created_at="2026-10-09T00:00:00.000Z",
    )
    tx.store(item)
    tx.write(
        "remember_sources", source.source_id, {"ref": source.model_dump(mode="json"), "valid": True}
    )
    owner = object.__new__(RememberPipeline)
    owner.uow = tx
    owner.identity = SimpleNamespace(
        authorize=lambda *a: None, revalidate=lambda *a: None, permits=lambda *a: True
    )
    owner.max_input_bytes = 65536
    reads = []

    def current(_tx, key):
        reads.append(key)
        if key != item.ref.memory_id:
            raise AssertionError("ordinary deletion must not hydrate other memories")
        from aether_agent_memory.runtime.contracts.models import RecordRef

        return MemorySnapshot.model_validate(
            tx.get(RecordRef.model_validate(tx.read("remember_current", key)))
        )

    owner.current = current
    owner.replay = lambda *a: ("operation", None)
    owner.remember_result = lambda *a: None
    owner.emit = lambda *a: None
    owner.enqueue = lambda _tx, _ctx, memory, kind: (
        kind.replace(".", "-") + "-" + memory.ref.memory_id
    )

    def change(_tx, memory, **updates):
        updated = memory.model_copy(update=updates)
        tx.store(updated)
        return updated

    owner.change = change
    ctx = SimpleNamespace(operation_id="delete", principal=SimpleNamespace(auth_epoch=1))
    return owner, tx, item, ctx, reads


def correction():
    return CorrectionRequest(
        expected_version=1,
        content="replacement",
        reason="edit",
        source=SourceInput(
            kind="text",
            external_id="edit",
            external_version="1",
            occurred_at="2026-10-09T00:00:00.000Z",
        ),
    )


@pytest.mark.parametrize("entry", ["current_ref", "prepare_correction", "correct"])
def test_working_correction_rejected_from_metadata_before_body_or_writes(entry):
    owner, tx, item, ctx, reads = fixture()
    before = deepcopy(tx.data)
    with pytest.raises(FoundationError) as error:
        if entry == "current_ref":
            owner.current_ref(tx, ctx, item.ref.memory_id)
        else:
            getattr(owner, entry)(ctx, item.ref.memory_id, correction())
    assert error.value.code == ErrorCode.INVALID_ARGUMENT
    assert "Working" in str(error.value)
    assert reads == []
    assert tx.data == before


@pytest.mark.parametrize("kind", ["episodic", "semantic"])
def test_ordinary_working_delete_preserves_long_term_and_other_users_without_scanning(kind):
    owner, tx, item, ctx, reads = fixture()
    fact = item.model_copy(
        update={
            "ref": item.ref.model_copy(update={"memory_id": "long-term"}),
            "kind": MemoryKind(kind),
        }
    )
    tx.store(fact)
    foreign = item.model_copy(
        update={
            "ref": item.ref.model_copy(
                update={
                    "memory_id": "bob",
                    "scope": item.ref.scope.model_copy(update={"user_id": "bob"}),
                }
            )
        }
    )
    tx.store(foreign)
    tx.write(
        "remember_relations",
        fact.ref.memory_id,
        {"working_refs": [item.ref.model_dump(mode="json")]},
    )
    before_fact = tx.get(memory_ref(fact.ref, versioned=True))
    before_foreign = tx.get(memory_ref(foreign.ref, versioned=True))
    tx.write(
        "remember_pending",
        item.ref.memory_id,
        {"ref": item.ref.model_dump(mode="json"), "state": "running", "task_id": "extract"},
    )
    tx.write("remember_artifacts", owner.refkey(item.ref), {"published": True})
    tx.write(
        "remember_working_summaries",
        item.ref.memory_id,
        {"memory": item.ref.model_dump(mode="json"), "state": "running"},
    )
    receipt = owner.delete_in(
        tx, ctx, item.ref.memory_id, DeleteRequest(expected_revision=1, reason="delete record")
    )
    assert receipt.task_ids == ("remember-cleanup-working",)
    assert tx.get(memory_ref(item.ref, versioned=True))["status"] == "deleted"
    assert tx.get(memory_ref(fact.ref, versioned=True)) == before_fact
    assert tx.get(memory_ref(foreign.ref, versioned=True)) == before_foreign
    assert tx.read("remember_sources", item.sources[0].source_id)["valid"]
    assert tx.read("remember_pending", item.ref.memory_id)["state"] == "obsolete"
    assert tx.read("remember_artifacts", owner.refkey(item.ref))["published"] is False
    assert tx.read("remember_working_summaries", item.ref.memory_id)["state"] == "obsolete"
    assert set(reads) == {item.ref.memory_id}
    assert "remember_current" not in tx.scans
    assert qualify(owner, tx, ctx, (fact.ref,), "recall").items[0].decision == "allowed"
    assert qualify(owner, tx, ctx, (item.ref,), "recall").items[0].reason == "deleted"


def test_long_term_correction_target_still_allowed():
    owner, tx, item, ctx, reads = fixture()
    fact = item.model_copy(update={"kind": MemoryKind.SEMANTIC})
    tx.store(fact)
    assert Remember.current_ref(owner, tx, ctx, fact.ref.memory_id) == fact.ref
    assert reads == []


def test_explicit_source_deletion_filters_metadata_before_loading_unrelated_cold_body():
    owner, tx, item, ctx, reads = fixture()
    source = item.sources[0]
    tx.write(
        "remember_sources",
        source.source_id,
        {
            "ref": source.model_dump(mode="json"),
            "valid": True,
            "revision": 1,
            "scope": item.ref.scope.model_dump(mode="json"),
        },
    )
    foreign = item.model_copy(
        update={
            "ref": item.ref.model_copy(
                update={
                    "memory_id": "bob",
                    "scope": item.ref.scope.model_copy(update={"user_id": "bob"}),
                }
            ),
            "sources": (source.model_copy(update={"source_id": "unrelated"}),),
        }
    )
    tx.store(foreign)
    owner.mutations = SimpleNamespace(
        begin=lambda *a, **k: SimpleNamespace(previous=None, finish=lambda *a, **k: None)
    )
    receipt = owner.delete_source(
        ctx, source.source_id, DeleteRequest(expected_revision=1, reason="withdraw source")
    )
    assert receipt.blocked
    assert not tx.read("remember_sources", source.source_id)["valid"]
    assert tx.get(memory_ref(foreign.ref, versioned=True))["status"] == "active"
    assert set(reads) == {item.ref.memory_id}


@pytest.mark.parametrize("stage_name", ["persist", "reconcile_persist", "commit"])
async def test_prepared_historical_working_correction_cannot_write_or_replay_body(
    stage_name, monkeypatch
):
    from aether_agent_memory.remember.basic.temporal_stages import CorrectionStages, SaveStages
    from aether_agent_memory.runtime.temporal.activities import StageContext

    owner, tx, item, ctx, reads = fixture()
    stage = CorrectionStages(SimpleNamespace(uow=tx), owner)
    called = []

    async def payload():
        return {"memory_id": item.ref.memory_id, "request": correction().model_dump(mode="json")}

    async def body_io(*args):
        called.append("body_io")
        return SimpleNamespace(outcome="deferred")

    stage.payload = payload
    monkeypatch.setattr(StageContext, "current", staticmethod(lambda: SimpleNamespace(context=ctx)))
    monkeypatch.setattr(SaveStages, "persist", body_io)
    monkeypatch.setattr(SaveStages, "reconcile_persist", body_io)
    with pytest.raises(FoundationError) as error:
        await getattr(stage, stage_name)(None)
    assert error.value.code == ErrorCode.INVALID_ARGUMENT
    assert called == reads == []


@pytest.mark.parametrize("state", ["pending", "running", "failed", "obsolete"])
def test_legacy_summary_state_cannot_block_full_original_projection(state):
    owner, tx, item, _, _ = fixture()
    tx.write(
        "remember_working_summaries",
        item.ref.memory_id,
        {"memory": item.ref.model_dump(mode="json"), "state": state},
    )
    assert owner.projection_buildable(tx, item)
    descriptor = item.model_copy(
        update={"content": "source reference", "content_hash": text_hash("source reference")}
    )
    assert not owner.projection_buildable(tx, descriptor), (
        "do not index a retained historical descriptor as original"
    )
