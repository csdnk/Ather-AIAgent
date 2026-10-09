"""Persisted summary continuations cannot rewrite immutable original Working."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.basic.summaries import WorkingSummaries
from aether_agent_memory.remember.basic.temporal_background import BackgroundStages
from aether_agent_memory.runtime.temporal.activities import StageContext

from .test_remember_projection_dispatch import case as case


@pytest.fixture
def historical(case):
    owner, ctx, working = case
    task = SimpleNamespace(
        task_id="legacy-summary",
        kind="remember.summarize",
        input_ref=memory_ref(working.ref, versioned=True),
        subject=memory_ref(working.ref),
    )
    forbidden = Mock(side_effect=AssertionError("retired summary used a body/model/writer"))
    owner.current = owner.change = owner.put = owner.emit = forbidden
    owner.bodies = SimpleNamespace(persist=forbidden, admit=forbidden)
    owner.tokenizer = SimpleNamespace(count=len)
    owner.source_access = SimpleNamespace(read=forbidden, checked=forbidden)
    provider = SimpleNamespace(select=forbidden)
    owner.summaries = WorkingSummaries(owner, provider)
    state = {
        "memory": working.ref.model_dump(mode="json"),
        "source": working.sources[0].model_dump(mode="json"),
        "task_id": task.task_id,
        "state": "running",
        "task_context": "legacy",
    }
    with owner.uow.transaction() as tx:
        tx.write("remember_working_summaries", working.ref.memory_id, state)
        tx.write(
            "remember_pending",
            working.ref.memory_id,
            {
                "ref": working.ref.model_dump(mode="json"),
                "state": "scheduled",
                "task_id": task.task_id,
                "context": ctx.model_dump(mode="json"),
                "bytes": len(working.content.encode()),
                "tokens": 1,
                "created_at": working.created_at,
            },
        )
    prepared = {
        "state": state,
        "source": state["source"],
        "chosen": [],
        "failure": None,
        "content": "old generated summary must not become Working v2",
    }
    return owner, ctx, working, task, prepared, forbidden


@pytest.mark.parametrize("phase", ["process", "generate", "commit", "commit_metadata"])
async def test_summary_continuations_retire_without_io_or_changing_working(historical, phase):
    owner, ctx, working, task, prepared, forbidden = historical
    with owner.uow.transaction() as tx:
        original = tx.get(memory_ref(working.ref, versioned=True))
    method = getattr(owner.summaries, phase)
    args = (ctx, task, working, prepared) if phase.startswith("commit") else (ctx, task, working)
    result = method(*args) if phase == "commit_metadata" else await method(*args)
    assert result.outcome == "obsolete"
    assert result.effect_status == "no_effect"
    forbidden.assert_not_called()
    with owner.uow.transaction() as tx:
        assert tx.get(memory_ref(working.ref, versioned=True)) == original
        assert len(tx.rows("remember_current")) == 1
        assert tx.read("remember_working_summaries", working.ref.memory_id)["state"] == "obsolete"
        pending = tx.read("remember_pending", working.ref.memory_id)
        assert pending["state"] == "pending"
        assert "task_id" not in pending
        assert pending["reschedule_operation_id"]
        assert tx.rows("tasks") == []


@pytest.mark.parametrize("phase", ["prepare", "generate", "publish", "commit"])
@pytest.mark.parametrize("handler", [False, True])
async def test_temporal_old_prepared_summary_is_rejected_before_any_body_or_generation_read(
    historical, monkeypatch, phase, handler
):
    owner, ctx, working, task, prepared, forbidden = historical
    context = SimpleNamespace(task=task, context=ctx, guard=owner.tasks.guard)
    monkeypatch.setattr(StageContext, "current", staticmethod(lambda: context))
    stages = BackgroundStages(owner)
    stages.items = stages.load = stages.generation = stages.required = forbidden
    owner.prepare_background = forbidden
    method = stages.handler(phase) if handler else getattr(stages, phase)
    result = await method(None) if handler else await method(None, reconcile=True)
    assert result.outcome == "obsolete"
    forbidden.assert_not_called()
    with owner.uow.transaction() as tx:
        assert tx.rows("temporal_body_writes") == []
        assert tx.read("remember_working_summaries", working.ref.memory_id)["state"] == "obsolete"


@pytest.mark.parametrize("pending_change", ["other_task", "processed", "other_version", "deleted"])
async def test_retired_summary_does_not_release_unrelated_processed_or_deleted_pending(
    historical, pending_change
):
    owner, ctx, working, task, prepared, forbidden = historical
    with owner.uow.transaction() as tx:
        pending = tx.read("remember_pending", working.ref.memory_id)
        if pending_change == "other_task":
            pending = {**pending, "task_id": "current-extraction"}
        elif pending_change == "processed":
            pending = {**pending, "state": "processed"}
        elif pending_change == "other_version":
            pending = {**pending, "ref": {**pending["ref"], "version": 2}}
        else:
            physical = memory_ref(working.ref, versioned=True)
            raw = tx.get(physical)
            tx.put_if_revision(physical, {**raw, "status": "deleted"}, tx.revision(physical))
        tx.write("remember_pending", working.ref.memory_id, pending)
    assert (await owner.summaries.process(ctx, task, working)).outcome == "obsolete"
    with owner.uow.transaction() as tx:
        assert tx.read("remember_pending", working.ref.memory_id) == pending
    forbidden.assert_not_called()
