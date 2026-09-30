"""Business durability and fencing do not depend on Temporal history retention."""

from hashlib import sha256

import pytest
from pydantic import ValidationError

from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    Flow,
    Permission,
    Principal,
    RecordRef,
    Scope,
    TaskSpec,
    TaskState,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.host import Foundation
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.models import ExecutionRef, StepRequest, StepResult


@pytest.fixture
def foundation(tmp_path):
    app = Foundation(tmp_path / "business.db", engineering_profile=True)
    person = Principal(
        principal_id="alice",
        auth_epoch=1,
        permissions=tuple(Permission),
        home_scope=Scope(
            tenant_id="tenant", application_id="app", user_id="alice", agent_id="agent"
        ),
    )
    app.identity.provision([(sha256(b"alice").hexdigest(), person)], ())
    yield app
    app.close()


@pytest.fixture
def ledger(foundation):
    return ExecutionLedger(
        foundation.tasks, TemporalConfiguration(deployment_id="test", endpoint="127.0.0.1:7233")
    )


def spec_for(app, key="first", text="input"):
    ctx = app.identity.context("alice")
    ref = RecordRef(
        owner=Flow.RUNTIME,
        object_type="engineering_input",
        object_id=key,
        scope=ctx.principal.home_scope,
    )
    content = {"text": text}
    spec = TaskSpec(
        task_id=key,
        owner_flow=Flow.RUNTIME,
        kind="engineering.save",
        subject=ref,
        input_ref=ref,
        idempotency_key=key,
        input_hash=fingerprint(content),
        initiator_id="alice",
        initiator_auth_epoch=1,
        deadline_at=ctx.deadline_at,
    )
    return ctx, ref, content, spec


def admit(app, ledger, key="first"):
    ctx, ref, content, spec = spec_for(app, key)
    with app.uow.transaction() as tx:
        tx.put_if_revision(ref, content, None)
        job = ledger.admit(tx, ctx, spec)
    return job, spec


def delivery(job, attempt=1, run="run1"):
    return ExecutionRef(
        namespace="default",
        workflow_id=f"p3/test/{job.kind}/{job.job_id}",
        run_id=run,
        activity_id="1",
        delivery_attempt=attempt,
        epoch=0,
    )


def test_admission_and_start_intent_commit_together(foundation, ledger):
    ctx, ref, content, spec = spec_for(foundation)
    with pytest.raises(RuntimeError), foundation.uow.transaction() as tx:
        tx.put_if_revision(ref, content, None)
        ledger.admit(tx, ctx, spec)
        assert len(tx.pending_intent_rows("start", limit=10)) == 1
        raise RuntimeError("crash before commit")
    with foundation.uow.transaction() as tx:
        assert tx.read("tasks", spec.task_id) is None
        assert tx.pending_intent_rows("start", limit=10) == []
        assert tx.get(ref) is None


def test_stale_epoch_cannot_commit(foundation, ledger):
    job, _ = admit(foundation, ledger)
    step = StepRequest(job=job, stage="save", ordinal=0, mode="execute")
    with foundation.uow.transaction() as tx:
        first = ledger.begin(tx, step, delivery(job))
    with foundation.uow.transaction() as tx:
        second = ledger.begin(tx, step, delivery(job, 2))
    assert second.epoch > first.epoch
    with pytest.raises(FoundationError) as error, foundation.uow.transaction() as tx:
        ledger.guard(tx, job.job_id, first)
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    with foundation.uow.transaction() as tx:
        current = ledger.guard(tx, job.job_id, second)
        assert current.lease is None
        assert current.execution == second


def test_terminal_idempotency_survives_history_removal(foundation, ledger):
    job, spec = admit(foundation, ledger)
    step = StepRequest(job=job, stage="save", ordinal=0, mode="execute")
    with foundation.uow.transaction() as tx:
        execution = ledger.begin(tx, step, delivery(job))
        output = spec.subject.model_copy(update={"object_type": "output"})
        tx.put_if_revision(output, {"value": 7}, None)
        ledger.complete(tx, job.job_id, execution, output)
    # No Temporal client/history exists. Durable business records still govern admission.
    ctx = foundation.identity.context("alice")
    with foundation.uow.transaction() as tx:
        repeated = ledger.admit(tx, ctx, spec)
        assert repeated.job_id == job.job_id
        assert foundation.tasks.load(tx, job.job_id)[1].state == TaskState.SUCCEEDED
        assert len(tx.rows("temporal_start_intents")) == 1
    with pytest.raises(FoundationError) as error, foundation.uow.transaction() as tx:
        ledger.admit(tx, ctx, spec.model_copy(update={"input_hash": "0" * 64}))
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_wrong_workflow_cannot_take_ownership(foundation, ledger):
    job, _ = admit(foundation, ledger)
    with pytest.raises(FoundationError) as error, foundation.uow.transaction() as tx:
        ledger.begin(
            tx,
            StepRequest(job=job, stage="save", ordinal=0, mode="execute"),
            delivery(job).model_copy(update={"workflow_id": "unrelated"}),
        )
    assert error.value.code == ErrorCode.VERSION_CONFLICT


def test_unknown_step_cannot_claim_success():
    with pytest.raises(ValidationError):
        StepResult(
            outcome="done",
            next_stage="publish",
            effect_status=EffectStatus.UNKNOWN,
            reason_code="timeout",
        )
