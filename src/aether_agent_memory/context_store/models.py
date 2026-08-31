from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.scope import Scope


class ContextItemKind(StrEnum):
    DIRECTORY = "directory"
    MEMORY = "memory"
    RESOURCE = "resource"
    SKILL = "skill"
    SESSION = "session"


class ContextVisibility(StrEnum):
    SESSION = "session"
    AGENT = "agent"
    USER = "user"
    TENANT = "tenant"


class ContextLayer(StrEnum):
    ABSTRACT = "L0"
    OVERVIEW = "L1"
    DETAIL = "L2"


class ContextLayerStatus(StrEnum):
    AVAILABLE = "available"
    PENDING = "pending"
    UNAVAILABLE = "unavailable"


class ContextProjectionWorkStatus(StrEnum):
    """Lifecycle of a derived semantic projection job."""

    PENDING = "pending"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class ContextProjectionWorkItem(BaseModel):
    """Typed, retryable work for projecting one logical Context item.

    The URI and source revision identify authoritative P3 data. The queue is
    only a delivery mechanism for derived representations and never becomes a
    second source of truth.
    """

    work_id: str = Field(default_factory=lambda: uuid4().hex)
    uri: AetherUri
    source_revision: int = Field(ge=1)
    scope: Scope
    layers: list[ContextLayer] = Field(
        default_factory=lambda: [ContextLayer.ABSTRACT, ContextLayer.OVERVIEW]
    )
    status: ContextProjectionWorkStatus = ContextProjectionWorkStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    claimed_at: datetime | None = None
    lease_until: datetime | None = None


class ContextContent(BaseModel):
    layer: ContextLayer
    status: ContextLayerStatus = ContextLayerStatus.AVAILABLE
    text: str | None = None
    content_ref: str | None = None
    token_estimate: int | None = Field(default=None, ge=0)
    derived: bool = True
    generator: str | None = None
    generated_at: datetime | None = None

    @model_validator(mode="after")
    def _available_content_has_payload(self) -> ContextContent:
        if (
            self.status == ContextLayerStatus.AVAILABLE
            and self.text is None
            and self.content_ref is None
        ):
            raise ValueError("available context content requires text or content_ref")
        return self


class ContextItem(BaseModel):
    """Logical context object independent of P2, Milvus, Redis, or B1."""

    uri: AetherUri
    kind: ContextItemKind
    visibility: ContextVisibility = ContextVisibility.SESSION
    scope: Scope
    title: str
    layers: dict[ContextLayer, ContextContent] = Field(default_factory=dict)
    source_revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _layer_keys_match_payload(self) -> ContextItem:
        mismatches = [key for key, value in self.layers.items() if key != value.layer]
        if mismatches:
            raise ValueError("context layer keys must match their payload layer")
        return self

    def content_for(self, layer: ContextLayer) -> ContextContent | None:
        return self.layers.get(layer)


class ContextSearchQuery(BaseModel):
    query: str
    scope: Scope
    trace_id: str | None = None
    root_uri: AetherUri | None = None
    kinds: list[ContextItemKind] = Field(default_factory=list)
    layers: list[ContextLayer] = Field(
        default_factory=lambda: [ContextLayer.ABSTRACT, ContextLayer.OVERVIEW]
    )
    limit: int = Field(default=20, gt=0, le=1000)


class ContextSearchHit(BaseModel):
    uri: AetherUri
    kind: ContextItemKind
    layer: ContextLayer
    score: float
    text: str | None = None
    content_ref: str | None = None
    source: str
    source_revision: int | None = Field(default=None, ge=1)


class ContextSearchResult(BaseModel):
    hits: list[ContextSearchHit] = Field(default_factory=list)
    backend: str
    complete: bool = True
    missing_sources: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextDeleteResult(BaseModel):
    """Provider-neutral result of deleting one authoritative Context object."""

    uri: AetherUri
    kind: ContextItemKind
    deleted: bool
    index_invalidated: bool = False


class ContextCandidate(BaseModel):
    """Provider-neutral candidate passed through the unified recall pipeline."""

    item_id: str
    uri: AetherUri
    kind: ContextItemKind
    layer: ContextLayer = ContextLayer.DETAIL
    content: str | None = None
    content_ref: str | None = None
    source: str
    score: float = 0.0
    semantic_score: float | None = None
    temporal_score: float | None = None
    created_at: datetime | None = None
    last_access: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalTraceAction(StrEnum):
    SOURCE_RECALL = "source_recall"
    CANDIDATE_SCORED = "candidate_scored"
    CONTEXT_SELECTED = "context_selected"
    BUDGET_SKIPPED = "budget_skipped"
    DEGRADED = "degraded"


class RetrievalTraceStep(BaseModel):
    action: RetrievalTraceAction
    source: str
    uri: AetherUri | None = None
    layer: ContextLayer | None = None
    score: float | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    candidate_count: int | None = Field(default=None, ge=0)
    reason: str | None = None


class RetrievalTrace(BaseModel):
    trace_id: str
    query: str
    scope: Scope
    target_uri: AetherUri | None = None
    steps: list[RetrievalTraceStep] = Field(default_factory=list)
    complete: bool = True
    missing_sources: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def record(self, step: RetrievalTraceStep) -> None:
        self.steps.append(step)

    def finish(self, *, complete: bool, missing_sources: list[str]) -> None:
        self.complete = complete
        self.missing_sources = list(missing_sources)
        self.finished_at = datetime.now(UTC)
