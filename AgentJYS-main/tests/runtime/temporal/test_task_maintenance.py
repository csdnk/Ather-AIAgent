from hashlib import sha256

import pytest
from test_ledger import admit, foundation, ledger  # noqa: F401

from aether_agent_memory.runtime.contracts.models import (
    PageRequest,
    Permission,
    Principal,
    RecoveryRequest,
    Scope,
)
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.temporal.controls import (
    ControlAdmission,
    authorize_delivery,
)


def test_configured_operator_task_access_does_not_grant_memory_or_trace(foundation, ledger):  # noqa: F811
    alice = foundation.identity.context("alice").principal
    operator = Principal(
        principal_id="ops",
        auth_epoch=1,
        home_scope=Scope(
            tenant_id="operations", user_id="ops", application_id="app", agent_id="agent"
        ),
        permissions=(Permission.DIAGNOSE, Permission.RECOVER),
    )
    outsider = operator.model_copy(update={"principal_id": "outsider"})
    foundation.identity.provision(
        [(sha256(p.principal_id.encode()).hexdigest(), p) for p in (alice, operator, outsider)]
    )
    foundation.diagnostics.maintenance_principals = ("ops",)
    foundation.tasks.maintenance_principals = ("ops",)
    controls = ControlAdmission(ledger, ("ops",))
    foundation.tasks.on_recovery = controls.recovery
    job, spec = admit(foundation, ledger)
    ctx = foundation.identity.context("ops")
    other = foundation.identity.context("outsider")
    page = foundation.diagnostics.tasks_page(ctx, PageRequest())
    assert [i["task_id"] for i in page["items"]] == [job.job_id]
    assert page["items"][0]["trace_id"] is None
    assert page["items"][0]["revision"] == foundation.diagnostics.task(ctx, job.job_id).revision
    assert foundation.diagnostics.task(ctx, job.job_id).task_id == job.job_id
    assert foundation.tasks.progress.read(ctx, job.job_id)["stages"] == []
    with pytest.raises(FoundationError):
        foundation.tasks.progress.read(other, job.job_id)
    assert foundation.diagnostics.tasks_page(other, PageRequest())["items"] == []
    with foundation.uow.transaction() as tx:
        assert not foundation.identity.permits(tx, ctx, Permission.READ, spec.subject)
        assert not foundation.identity.permits(tx, ctx, Permission.DIAGNOSE, spec.subject)
        stored = tx.read("temporal_bindings", job.job_id)
        binding = {
            "namespace": stored["namespace"],
            "workflow_id": stored["workflow_id"],
            "first_run_id": "run",
            "current_run_id": "run",
            "input_hash": job.input_hash,
            "plan_version": job.plan_version,
        }
        tx.write("temporal_bindings", job.job_id, {**stored, "binding": binding})
    with foundation.uow.transaction() as tx:
        operation = foundation.tasks.request_recovery(
            tx,
            ctx,
            RecoveryRequest(
                operation_id="recover",
                task_id=job.job_id,
                expected_revision=1,
                reason="recovery test",
            ),
        )
    assert controls.status(ctx, operation.operation_id).operation_id == "recover"
    with pytest.raises(FoundationError):
        controls.status(other, operation.operation_id)
    with foundation.uow.transaction() as tx:
        row = tx.rows("temporal_control_intents")[0][1]
        authorize_delivery(ledger, tx, row)
    ledger.periodic_operators = ()
    with pytest.raises(FoundationError), foundation.uow.transaction() as tx:
        authorize_delivery(ledger, tx, row)
