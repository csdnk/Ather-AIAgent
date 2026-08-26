"""Typed DTOs used across P3 ports and application services."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ObjectReference(BaseModel):
    p2_bucket: str
    object_key: str
    content_ref: str


class LongMemorySubmission(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    memory_id: str
    state: str
    request_id: str
    trace_id: str
    source_id: str
    object_id: str | None = None
    content_ref: str | None = None
    p2_bucket: str | None = None
    object_key: str | None = None
    formation: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> LongMemorySubmission:
        return cls.model_validate(payload)

    def to_response_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class TaskStatusRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    state: str
    memory_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    source_id: str | None = None
    object_id: str | None = None
    content_ref: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    chunk_count: int | None = None
    p2_vector_count: int | None = None
    p2_collection: str | None = None
    error: str | None = None
    compression_artifact_id: str | None = None
    compression_status: str | None = None
    compression_algorithm: str | None = None
    compression_scorer: str | None = None
    compression_ratio: float | None = None
    compression_rate: float | None = None
    compression_warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> TaskStatusRecord:
        return cls.model_validate(payload)

    def to_response_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class MemorySearchHit(BaseModel):
    memory_id: str | None = None
    task_id: str | None = None
    chunk_id: str | None = None
    text: str = ""
    score: float = 0.0
    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    category: str | None = None
    keywords: list[str] = Field(default_factory=list)
    content_ref: str | None = None
    trace_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> MemorySearchHit:
        return cls.model_validate(payload)


class MemorySearchResult(BaseModel):
    items: list[MemorySearchHit] = Field(default_factory=list)
    backend: str
    collection: str | None = None
    query_model: str | None = None
    query_dimension: int | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> MemorySearchResult:
        return cls.model_validate(payload)

    def to_response_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class VectorQueryResult(BaseModel):
    vector: list[float]
    dimension: int
    model: str | None = None
    backend: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
