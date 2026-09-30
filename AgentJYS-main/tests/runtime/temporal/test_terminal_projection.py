import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger
from test_temporal_http import configuration

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.runtime.contracts.foundation import SignalObservation
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    OperationRecord,
    RecordRef,
    TaskState,
)
from aether_agent_memory.runtime.flows.application import Service
from aether_agent_memory.runtime.foundation.common import FoundationError, now
from aether_agent_memory.runtime.temporal.activities import Activities, StageContext
from aether_agent_memory.runtime.temporal.locking import ExecutionLimits
from aether_agent_memory.runtime.temporal.models import (
    CloseRequest,
    ExecutionRef,
    ExecutionStatus,
    StepRequest,
    StepResult,
    WorkflowBinding,
)
from aether_agent_memory.runtime.temporal.periodic import PeriodicActivities


@pytest.fixture
def service(tmp_path):
    app = Service(configuration(tmp_path, "127.0.0.1:1"))
    yield app
    asyncio.run(app.close())


@pytest.mark.parametrize("path", ["close", "record", "diagnostic"])
@pytest.mark.parametrize("unknown", [False, True])
def test_terminal_recall_and_operation_converge_idempotently(service, path, unknown):
    execution = service.execution
    ledger, tasks = execution.ledger, execution.ledger.tasks
    ctx = service.runtime.foundation.identity.context("alice")
    job = execution.recall.accept(
        ctx, RecallRequest(query="closure", sources="long_term", selection={})
    )
    step = StepRequest(job=job, stage="prepare", ordinal=0, mode="execute")
    with tasks.uow.transaction() as tx:
        stored = tx.read("temporal_bindings", job.job_id)
        fence = ledger.begin(
            tx,
            step,
            ExecutionRef(
                namespace="default",
                workflow_id=stored["workflow_id"],
                run_id="first",
                activity_id="first",
                delivery_attempt=1,
                epoch=0,
            ),
        )
        row, task = tasks.load(tx, job.job_id)
        task = tasks.change(
            tx, row, task, effect_status=EffectStatus.UNKNOWN if unknown else EffectStatus.NO_EFFECT
        )
        binding = WorkflowBinding(
            namespace="default",
            workflow_id=stored["workflow_id"],
            first_run_id="first",
            current_run_id="first",
            input_hash=job.input_hash,
            plan_version="1",
        )
        stored = tx.read("temporal_bindings", job.job_id)
        tx.write(
            "temporal_bindings", job.job_id, {**stored, "binding": binding.model_dump(mode="json")}
        )
        op = OperationRecord(
            operation_id="recover",
            subject=task.subject,
            phase="recovery",
            state="accepted",
            task_ids=(task.task_id,),
        )
        tx.write(
            "operations", "recover", {"record": op.model_dump(mode="json"), "signature": "test"}
        )
    service.runtime.recall.stage(ctx, job.job_id, "discover")
    failure = StepResult(
        outcome="attention" if unknown else "failed",
        effect_status=task.effect_status,
        reason_code="DEADLINE_EXCEEDED",
    )
    with ThreadPoolExecutor(1) as pool:
        activities = Activities(
            ledger, execution.registry, ExecutionLimits(tasks.class_limits), pool
        )
        if path == "record":
            activities.record(step, StageContext(ledger, task, ctx, fence, pool), failure)
        elif path == "diagnostic":
            with tasks.uow.transaction() as tx:
                PeriodicActivities(ledger, None).diagnose(
                    tx, binding, ExecutionStatus(state="terminated", run_id="first")
                )
        else:
            activities.close(CloseRequest(job=job, result=failure))
        # Verify before repeating closure: a direct stage terminal must already project.
        record = service.runtime.recall.status(ctx, job.job_id)
        assert record.state == "failed" and record.reason
        with pytest.raises(FoundationError) as result:
            service.runtime.recall.result(ctx, job.job_id)
        assert result.value.code != ErrorCode.REQUEST_IN_PROGRESS
        with tasks.uow.transaction() as tx:
            before = (tx.read("recall_requests", job.job_id), tx.read("operations", "recover"))
            assert before[1]["record"]["state"] == ("unknown" if unknown else "failed")
            assert tasks.load(tx, job.job_id)[1].effect_status == task.effect_status
        activities.close(CloseRequest(job=job, result=failure))
        with tasks.uow.transaction() as tx:
            assert (
                tx.read("recall_requests", job.job_id),
                tx.read("operations", "recover"),
            ) == before
        with pytest.raises(FoundationError), tasks.uow.transaction() as tx:
            ledger.begin(tx, step, fence.model_copy(update={"activity_id": "late"}))
    assert service.runtime.recall.status(ctx, job.job_id).state == "failed"


def test_failed_repair_has_explicit_incident_and_operation_outcome(service):
    runtime, ledger = service.runtime, service.execution.ledger
    ctx = runtime.foundation.identity.context("alice")
    maintenance = service.execution.maintenance
    runtime.foundation.dispositions.configure_rule(ctx, maintenance.rule)
    subject = RecordRef(
        owner="operate", object_type="cache_copy", object_id="copy", scope=ctx.principal.home_scope
    )
    with runtime.foundation.uow.transaction() as tx:
        tx.put_if_revision(subject, {"readback": "corrupt"}, None)
    incident = runtime.foundation.dispositions.observe(
        ctx,
        subject,
        SignalObservation(
            signal=maintenance.signal,
            observed_at=now(),
            state="known",
            value="corrupt",
            labels={},
            evidence_refs=(subject,),
        ),
    )[0]
    with runtime.foundation.uow.transaction() as tx:
        task_id = tx.read("incidents", incident.incident_id)["task_id"]
        job = ledger.bind_admitted(tx, ledger.tasks.load(tx, task_id)[1])
    with ThreadPoolExecutor(1) as pool:
        activities = Activities(
            ledger, service.execution.registry, ExecutionLimits(ledger.tasks.class_limits), pool
        )
        close = CloseRequest(
            job=job,
            result=StepResult(
                outcome="failed",
                effect_status=EffectStatus.NO_EFFECT,
                reason_code="DEADLINE_EXCEEDED",
            ),
        )
        activities.close(close)
        result = runtime.foundation.dispositions.incidents(ctx)[0]
        assert result.state == "attention_required"
        assert result.verification in {"failed", "unknown"}
        assert result.reason_code == "DEADLINE_EXCEEDED"
        assert runtime.foundation.diagnostics.operation(ctx, task_id).state == "failed"
        activities.close(close)
        assert runtime.foundation.dispositions.incidents(ctx)[0] == result


def test_failed_event_task_does_not_leave_delivery_pending(foundation, ledger):
    from test_event_workflow import setup

    setup(foundation, ledger, lambda tx, event: None)
    with foundation.uow.transaction() as tx:
        key, row = tx.rows("tasks")[0]
        _, task = foundation.tasks.load(tx, key)
        foundation.tasks.change(
            tx, row, task, state=TaskState.FAILED, error_code=ErrorCode.DEADLINE_EXCEEDED
        )
        delivery = tx.read("deliveries", key)
        assert delivery["state"] == "attention_required"
        assert delivery["error_code"] == "DEADLINE_EXCEEDED"
        assert tx.pending_delivery_rows() == []
        assert tx.read("inbox", key) is None
