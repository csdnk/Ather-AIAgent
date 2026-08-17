from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from aether_agent_memory.core.enums import SourceType


class ProcessingStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class EmbeddingRequest(BaseModel):
    text: str
    source_type: SourceType
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    tenant_id: str | None = None
    source_id: str | None = None
    object_id: str | None = None
    doc_id: str | None = None
    memory_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TextChunk(BaseModel):
    chunk_id: str
    source_id: str
    text: str
    index: int
    start_char: int
    end_char: int


class EmbeddingRecord(BaseModel):
    request_id: str
    trace_id: str
    source_id: str
    object_id: str | None = None
    chunk_id: str
    chunk_text: str
    vector: list[float]
    embedding_model: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmbeddingResult(BaseModel):
    request_id: str
    trace_id: str
    source_id: str
    status: ProcessingStatus
    records: list[EmbeddingRecord] = Field(default_factory=list)
    latency_ms: float = Field(ge=0.0)
    error_code: str | None = None
    error_message: str | None = None
