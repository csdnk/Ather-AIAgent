from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Self, cast
from uuid import uuid4

from pydantic import BaseModel, Field, model_serializer, model_validator

from aether_agent_memory.core.enums import MemoryState, MemoryType, SourceType, StorageTier


class MemoryProjection(BaseModel):
    """Derived representation state, independent of a concrete embedding backend."""

    embedding: list[float] | None = None
    embedding_status: str = "pending"
    vector_projection_status: str = "pending"


class MemoryProcessingState(BaseModel):
    """Asynchronous processing state for compression/projection pipelines."""

    compression_artifact_id: str | None = None
    compression_status: str = "not_applicable"
    compression_ratio: float | None = None


class MemoryPlacement(BaseModel):
    """Physical placement of a memory representation in a provider-neutral form."""

    provider: str = "memory"
    segment_id: str
    object_key: str
    tier: StorageTier = StorageTier.L0_DRAM
    content_ref: str | None = None
    namespace: str | None = None


class MemoryValueState(BaseModel):
    """Value/heat state used by control-plane policies such as B3."""

    importance: float = 1.0
    access_count: int = 0
    last_accessed_at: datetime | None = None
    scheduler_signal_status: str = "pending"
    heat: float | None = None


class MemoryFact(BaseModel):
    kind: str
    subject: str
    predicate: str
    value: str
    normalized_key: str
    group_key: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_memory_id: str | None = None
    source_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    revision: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Memory(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_infrastructure_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if payload.get("placement") is None and payload.get("p2_ref") is not None:
            p2_ref = dict(payload["p2_ref"])
            p2_ref.setdefault("provider", "p2")
            payload["placement"] = p2_ref
        projection = dict(payload.get("projection") or {})
        for field in ("embedding", "embedding_status", "vector_projection_status"):
            if field in payload and field not in projection:
                projection[field] = payload[field]
        if projection:
            payload["projection"] = projection
        processing = dict(payload.get("processing") or {})
        for field in (
            "compression_artifact_id",
            "compression_status",
            "compression_ratio",
        ):
            if field in payload and field not in processing:
                processing[field] = payload[field]
        if processing:
            payload["processing"] = processing
        value_state = dict(payload.get("value_state") or {})
        for field in ("importance", "access_count", "last_accessed_at", "scheduler_signal_status"):
            if field in payload and field not in value_state:
                value_state[field] = payload[field]
        if value_state:
            payload["value_state"] = value_state
        return payload

    id: str = Field(default_factory=lambda: uuid4().hex)
    type: MemoryType
    state: MemoryState = MemoryState.ACTIVE
    session_id: str
    agent_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    task_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    source_id: str | None = None
    object_id: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: SourceType = SourceType.USER
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    superseded_by: str | None = None
    tags: list[str] = Field(default_factory=list)
    facts: list[MemoryFact] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    fact_bundle_version: str | None = None
    consolidation_status: str | None = None
    consolidated_from: list[str] = Field(default_factory=list)
    projection: MemoryProjection = Field(default_factory=MemoryProjection)
    processing: MemoryProcessingState = Field(default_factory=MemoryProcessingState)
    placement: MemoryPlacement | None = None
    value_state: MemoryValueState = Field(default_factory=MemoryValueState)

    @model_serializer(mode="wrap")
    def _serialize_with_legacy_aliases(self, handler: Any) -> dict[str, Any]:
        data = cast(dict[str, Any], handler(self))
        data.update(
            {
                "embedding": self.embedding,
                "embedding_status": self.embedding_status,
                "vector_projection_status": self.vector_projection_status,
                "compression_artifact_id": self.compression_artifact_id,
                "compression_status": self.compression_status,
                "compression_ratio": self.compression_ratio,
                "importance": self.importance,
                "access_count": self.access_count,
                "last_accessed_at": self.last_accessed_at,
                "scheduler_signal_status": self.scheduler_signal_status,
                "p2_ref": self.p2_ref,
            }
        )
        return data

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        return super().model_copy(
            update=self._nested_update_from_legacy(update),
            deep=deep,
        )

    def _nested_update_from_legacy(
        self,
        update: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        if update is None:
            return None
        nested = dict(update)
        projection = dict(
            nested.pop("projection", self.projection.model_copy(deep=True).model_dump())
        )
        for field in ("embedding", "embedding_status", "vector_projection_status"):
            if field in nested:
                projection[field] = nested.pop(field)
        nested["projection"] = MemoryProjection.model_validate(projection)

        processing = dict(
            nested.pop("processing", self.processing.model_copy(deep=True).model_dump())
        )
        for field in (
            "compression_artifact_id",
            "compression_status",
            "compression_ratio",
        ):
            if field in nested:
                processing[field] = nested.pop(field)
        nested["processing"] = MemoryProcessingState.model_validate(processing)

        value_state = dict(
            nested.pop("value_state", self.value_state.model_copy(deep=True).model_dump())
        )
        for field in (
            "importance",
            "access_count",
            "last_accessed_at",
            "scheduler_signal_status",
        ):
            if field in nested:
                value_state[field] = nested.pop(field)
        nested["value_state"] = MemoryValueState.model_validate(value_state)

        if "p2_ref" in nested:
            p2_ref = nested.pop("p2_ref")
            nested["placement"] = (
                None
                if p2_ref is None
                else MemoryPlacement.model_validate({"provider": "p2", **dict(p2_ref)})
            )
        return nested

    @property
    def embedding(self) -> list[float] | None:
        return self.projection.embedding

    @embedding.setter
    def embedding(self, value: list[float] | None) -> None:
        self.projection.embedding = value

    @property
    def embedding_status(self) -> str:
        return self.projection.embedding_status

    @embedding_status.setter
    def embedding_status(self, value: str) -> None:
        self.projection.embedding_status = value

    @property
    def vector_projection_status(self) -> str:
        return self.projection.vector_projection_status

    @vector_projection_status.setter
    def vector_projection_status(self, value: str) -> None:
        self.projection.vector_projection_status = value

    @property
    def compression_artifact_id(self) -> str | None:
        return self.processing.compression_artifact_id

    @compression_artifact_id.setter
    def compression_artifact_id(self, value: str | None) -> None:
        self.processing.compression_artifact_id = value

    @property
    def compression_status(self) -> str:
        return self.processing.compression_status

    @compression_status.setter
    def compression_status(self, value: str) -> None:
        self.processing.compression_status = value

    @property
    def compression_ratio(self) -> float | None:
        return self.processing.compression_ratio

    @compression_ratio.setter
    def compression_ratio(self, value: float | None) -> None:
        self.processing.compression_ratio = value

    @property
    def importance(self) -> float:
        return self.value_state.importance

    @importance.setter
    def importance(self, value: float) -> None:
        self.value_state.importance = value

    @property
    def access_count(self) -> int:
        return self.value_state.access_count

    @access_count.setter
    def access_count(self, value: int) -> None:
        self.value_state.access_count = value

    @property
    def last_accessed_at(self) -> datetime | None:
        return self.value_state.last_accessed_at

    @last_accessed_at.setter
    def last_accessed_at(self, value: datetime | None) -> None:
        self.value_state.last_accessed_at = value

    @property
    def scheduler_signal_status(self) -> str:
        return self.value_state.scheduler_signal_status

    @scheduler_signal_status.setter
    def scheduler_signal_status(self, value: str) -> None:
        self.value_state.scheduler_signal_status = value

    @property
    def p2_ref(self) -> MemoryPlacement | None:
        if self.placement is None or self.placement.provider != "p2":
            return None
        return self.placement

    @p2_ref.setter
    def p2_ref(self, value: MemoryPlacement | dict[str, Any] | None) -> None:
        if value is None:
            self.placement = None
            return
        if isinstance(value, MemoryPlacement):
            self.placement = value.model_copy(update={"provider": "p2"})
            return
        self.placement = MemoryPlacement.model_validate({"provider": "p2", **value})

    def touch(self) -> None:
        now = datetime.now(UTC)
        self.last_accessed_at = now
        self.access_count += 1
        self.updated_at = now

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires_at


class RecalledMemory(BaseModel):
    memory: Memory
    score: float


def normalize_projection_state(memory: Memory) -> Memory:
    """Keep top-level projection fields and metadata consistent.

    The top-level fields are authoritative; when the async pipeline wrote only
    one side, the other side is filled in so the two can never disagree.  A real
    value beats a default (``pending`` / ``not_applicable``).
    """
    meta = dict(memory.metadata)
    memory = _sync_projection(memory, meta, "embedding_status", default="pending")
    memory = _sync_projection(
        memory, meta, "compression_status", default="not_applicable"
    )
    memory = _sync_projection(memory, meta, "vector_projection_status", default="pending")
    memory = _sync_projection(memory, meta, "scheduler_signal_status", default="pending")
    return memory.model_copy(update={"metadata": meta})


def _sync_projection(
    memory: Memory, meta: dict[str, Any], field: str, *, default: str
) -> Memory:
    top = str(getattr(memory, field))
    meta_value = meta.get(field)
    if top == default and meta_value:
        setattr(memory, field, str(meta_value))
    else:
        meta[field] = top
    return memory
