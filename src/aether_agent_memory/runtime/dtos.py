"""Typed DTOs used across P3 ports and application services."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ObjectReference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = "p2"
    namespace: str | None = None
    object_key: str
    content_ref: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_mapping(cls, payload: object) -> object:
        if not isinstance(payload, dict):
            return payload
        data = dict(payload)
        provider_metadata = dict(data.get("metadata") or {})
        legacy_bucket = data.pop("p2_bucket", None)
        if legacy_bucket is not None:
            data.setdefault("namespace", legacy_bucket)
            provider_metadata.setdefault("bucket", legacy_bucket)
            data.setdefault("provider", "p2")
        data["metadata"] = provider_metadata
        return data


class LongMemorySubmission(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    memory_id: str
    state: str
    request_id: str
    trace_id: str
    source_id: str
    object_id: str | None = None
    content_ref: str | None = None
    provider: str | None = None
    namespace: str | None = None
    object_key: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    formation: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_mapping(cls, payload: object) -> object:
        if not isinstance(payload, dict):
            return payload
        data = dict(payload)
        provider_metadata = dict(data.get("provider_metadata") or {})
        legacy_bucket = data.pop("p2_bucket", None)
        if legacy_bucket is not None:
            data.setdefault("provider", "p2")
            data.setdefault("namespace", legacy_bucket)
            provider_metadata.setdefault("bucket", legacy_bucket)
        data["provider_metadata"] = provider_metadata
        return data

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> LongMemorySubmission:
        return cls.model_validate(payload)

    def to_response_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class TaskStatusRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    projection_count: int | None = None
    projection_namespace: str | None = None
    provider: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    compression_artifact_id: str | None = None
    compression_status: str | None = None
    compression_algorithm: str | None = None
    compression_scorer: str | None = None
    compression_ratio: float | None = None
    compression_rate: float | None = None
    compression_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_mapping(cls, payload: object) -> object:
        if not isinstance(payload, dict):
            return payload
        data = dict(payload)
        provider_metadata = dict(data.get("provider_metadata") or {})
        legacy_count = data.pop("p2_vector_count", None)
        legacy_collection = data.pop("p2_collection", None)
        if legacy_count is not None:
            data.setdefault("projection_count", legacy_count)
            provider_metadata.setdefault("vector_count", legacy_count)
        if legacy_collection is not None:
            data.setdefault("projection_namespace", legacy_collection)
            provider_metadata.setdefault("collection", legacy_collection)
        if legacy_count is not None or legacy_collection is not None:
            data.setdefault("provider", "p2")
        data["provider_metadata"] = provider_metadata
        return data

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
    source_revision: int | None = Field(default=None, ge=1)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> MemorySearchHit:
        return cls.model_validate(payload)


class MemorySearchResult(BaseModel):
    items: list[MemorySearchHit] = Field(default_factory=list)
    backend: str
    provider: str | None = None
    namespace: str | None = None
    query_model: str | None = None
    query_dimension: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_mapping(cls, payload: object) -> object:
        if not isinstance(payload, dict):
            return payload
        data = dict(payload)
        metadata = dict(data.get("metadata") or {})
        legacy_collection = data.pop("collection", None)
        if legacy_collection is not None:
            data.setdefault("namespace", legacy_collection)
            metadata.setdefault("collection", legacy_collection)
        data["metadata"] = metadata
        return data

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
