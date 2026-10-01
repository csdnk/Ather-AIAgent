"""Explicit browser contract: no credentials, headers or arbitrary diagnostics."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from aether_p4_simulator.validation.calls import ApiCall
from aether_p4_simulator.validation.models import ContextPack, MemoryRef

ScenarioID = Literal[
    "library-basic",
    "library-full",
    "weather-weekend",
    "preference-update",
    "learning-review",
    "forget-sources",
]
RunState = Literal["queued", "running", "passed", "failed", "blocked", "unconfirmed"]
StepState = Literal["pending", "running", "passed", "failed", "blocked", "unconfirmed", "skipped"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StartRequest(Model):
    scenario_id: ScenarioID
    request_id: UUID


class Check(Model):
    name: str
    passed: bool
    detail: str


class ProjectionEvidence(Model):
    memory_id: str
    memory_status: str
    projection_state: str
    processing_state: str


class Evidence(Model):
    method: Literal["GET", "POST", "PUT"] = "POST"
    path: str
    operation_id: str
    job_id: str | None = None
    memories: list[MemoryRef] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    recall_id: str | None = None
    elapsed_ms: float = 0
    processing: list[ProjectionEvidence] = Field(default_factory=list)
    context: ContextPack | None = None
    calls: list[ApiCall] = Field(default_factory=list)
    tasks: list["TaskEvidence"] = Field(default_factory=list)
    cleanup_state: str | None = None


class TaskEvidence(Model):
    task_id: str
    kind: str
    state: str
    effect_status: str
    error_code: str | None = None
    completed_parts: int = 0


class CoverageEntry(Model):
    method: str
    path: str
    state: Literal["unexecuted", "called", "passed", "failed", "blocked"] = "unexecuted"
    calls: int = 0
    http_statuses: list[int] = Field(default_factory=list)
    step_ids: list[int] = Field(default_factory=list)
    detail: str = "本轮未执行"


class Diagnostics(Model):
    state: Literal["pending", "complete", "incomplete"] = "pending"
    task_count: int = 0
    trace_count: int = 0
    log_record_count: int = 0
    incident_count: int = 0
    notes: list[str] = Field(default_factory=list)


class StepSnapshot(Model):
    id: int
    user_text: str
    state: StepState = "pending"
    response_text: str = ""
    checks: list[Check] = Field(default_factory=list)
    evidence: Evidence | None = None


class Mode(Model):
    profile: str
    embedding: str
    semantic_processing: str
    object_storage: str
    scheduling: str
    executor: str


class SafeError(Model):
    status: int
    code: str
    message: str
    operation_id: str | None = None
    write_outcome: Literal["not_applicable", "unconfirmed"] = "not_applicable"


class RunSnapshot(Model):
    run_id: str
    scenario_id: ScenarioID = "library-basic"
    state: RunState = "queued"
    current_step: int = 0
    total_steps: int = Field(default=6, ge=1, le=12)
    mode: Mode | None = None
    steps: list[StepSnapshot]
    error: SafeError | None = None
    coverage: list[CoverageEntry] = Field(default_factory=list)
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)
