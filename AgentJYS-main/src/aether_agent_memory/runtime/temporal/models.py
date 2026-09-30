"""Small immutable wire payloads. Business content stays in P3 storage."""

from typing import Literal, Self

from pydantic import Field, model_validator

from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Count,
    Digest,
    EffectStatus,
    Identifier,
    NonEmpty,
    Positive,
    RecordRef,
    Timestamp,
)
from aether_agent_memory.runtime.contracts.models import ExecutionRef as ExecutionRef
from aether_agent_memory.runtime.contracts.models import WorkflowBinding as WorkflowBinding


class WorkflowInput(ContractModel):
    job_id: Identifier
    kind: NonEmpty
    deployment_id: NonEmpty
    input_hash: Digest
    plan_version: NonEmpty = "1"
    entry: Literal["execute", "reconcile"] = "execute"


class StartIntent(ContractModel):
    intent_id: Identifier
    job: WorkflowInput
    task_queue: NonEmpty


class ControlIntent(ContractModel):
    control_id: Identifier
    job_id: Identifier
    action: Literal["cancel", "reconcile"]
    expected_revision: Positive


class StepRequest(ContractModel):
    job: WorkflowInput
    stage: NonEmpty
    ordinal: Count
    mode: Literal["execute", "reconcile"]


class StepResult(ContractModel):
    outcome: Literal["done", "retry", "query", "obsolete", "failed", "attention"]
    next_stage: NonEmpty | None = None
    result_ref: RecordRef | None = None
    effect_status: EffectStatus
    original_operation_id: str | None = None
    reason_code: NonEmpty

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.effect_status == EffectStatus.UNKNOWN and self.outcome not in {
            "query",
            "attention",
        }:
            raise ValueError("unknown effects require query or attention")
        if self.outcome == "done" and self.result_ref is None and self.next_stage is None:
            raise ValueError("done requires a durable result or next stage")
        return self


class ExecutionStatus(ContractModel):
    state: Literal[
        "running", "completed", "failed", "cancelled", "terminated", "timed_out", "not_found"
    ]
    run_id: str | None = None


class StagePolicy(ContractModel):
    effect_mode: Literal["read", "idempotent", "uncertain"]
    timeout_seconds: float = Field(default=30, gt=0, le=3600)


class TaskPlan(ContractModel):
    first_stage: NonEmpty
    stages: dict[str, StagePolicy]
    deadline_at: Timestamp
    query_deadline_at: Timestamp
    retry_seconds: float = Field(gt=0, le=60)


class CloseRequest(ContractModel):
    job: WorkflowInput
    result: StepResult
    controls: tuple[ControlIntent, ...] = ()


class PeriodicState(ContractModel):
    deployment_id: NonEmpty
    last_tick: int = -1
    cursor: str | None = None
    acknowledged_controls: tuple[str, ...] = ()
    interval_seconds: float = Field(default=5, gt=0, le=86400)
    batch_size: int = Field(default=32, ge=1, le=100)
    continue_after: int = Field(default=1000, ge=1, le=1000)
