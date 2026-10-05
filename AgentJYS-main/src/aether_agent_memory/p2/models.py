"""Vector-write transport record consumed by the current P2 RPC adapter."""

from typing import Any

from pydantic import BaseModel, Field


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
