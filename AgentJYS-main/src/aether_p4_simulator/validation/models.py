"""Typed current-P3 adapter responses and operation-ID validation."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from aether_agent_memory.recall.contracts.models import ContextPack as ContextPack
from aether_agent_memory.recall.contracts.models import RecallRequest as RecallRequest
from aether_agent_memory.remember.contracts.models import CorrectionRequest as CorrectionRequest
from aether_agent_memory.remember.contracts.models import MemoryRef as MemoryRef
from aether_agent_memory.remember.contracts.models import MemorySnapshot as MemorySnapshot
from aether_agent_memory.remember.contracts.models import RememberReceipt as RememberReceipt
from aether_agent_memory.remember.contracts.models import RememberRequest as RememberRequest
from aether_agent_memory.remember.contracts.models import SourceRef as SourceRef
from aether_agent_memory.runtime.contracts.foundation import (
    RuntimeHealthSnapshot as RuntimeHealthSnapshot,
)
from aether_agent_memory.runtime.contracts.models import Identifier as Identifier
from aether_agent_memory.runtime.contracts.models import RecordRef as RecordRef
from aether_agent_memory.runtime.contracts.models import ScopeSelector as ScopeSelector
from aether_agent_memory.runtime.contracts.models import TaskRecord as TaskRecord
from aether_agent_memory.runtime.contracts.models import Timestamp as Timestamp

OperationID = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,128}$")]


class ResponseModel(BaseModel):
    # Expose named fields only, never arbitrary upstream diagnostic payloads.
    model_config = ConfigDict(extra="ignore", frozen=True, allow_inf_nan=False)


class ConsolidateData(ResponseModel):
    task_ids: tuple[Identifier, ...]


class ProcessingTask(ResponseModel):
    task_id: Identifier
    kind: str
    state: str | None
    error_code: str | None
    result_ref: RecordRef | None


class WorkingSummaryData(ResponseModel):
    memory: MemoryRef
    source: SourceRef
    state: str
    representation: str
    task_id: Identifier | None = None
    is_complete: StrictBool | None = None


class ProcessingData(ResponseModel):
    memory: MemoryRef
    state: str
    state_basis: str
    projection_state: str
    memory_status: str
    derived_memory_ids: tuple[Identifier, ...]
    tasks: tuple[ProcessingTask, ...]
    historical_failed_tasks: Annotated[StrictInt, Field(ge=0)]
    working_summary: WorkingSummaryData | None = None


class LiveData(ResponseModel):
    liveness: Literal["alive"]
    checked_at: Timestamp


class CapabilitiesData(ResponseModel):
    profile: str
    embedding: str
    semantic_processing: str
    object_storage: str
    scheduling: str
    executor: str
    operations: tuple[str, ...]


class ProbeError(ResponseModel):
    code: str
    message: str


class ProbeResult[T](ResponseModel):
    ok: StrictBool
    status_code: Annotated[StrictInt, Field(ge=100, le=599)] | None
    data: T | None
    error: ProbeError | None


class StatusData(ResponseModel):
    live: ProbeResult[LiveData]
    health: ProbeResult[RuntimeHealthSnapshot]
    ready: ProbeResult[RuntimeHealthSnapshot]
    capabilities: ProbeResult[CapabilitiesData]
