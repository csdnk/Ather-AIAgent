from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from aether_agent_memory.core.scope import Scope


class ResourceStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class ResourceRecord(BaseModel):
    """A P3-owned resource descriptor and its authoritative content reference."""

    resource_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    scope: Scope
    category: str = "documents"
    media_type: str = "text/plain"
    description: str = ""
    overview: str = ""
    content: str | None = None
    content_ref: str | None = None
    source: str = "p3"
    status: ResourceStatus = ResourceStatus.READY
    revision: int = Field(default=1, ge=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
