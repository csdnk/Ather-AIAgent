from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from aether_agent_memory.b2 import MemoryEvent, MemoryEventType
from aether_agent_memory.core.enums import SourceType
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.session.models import SessionArchive


class MemoryExtractionCandidate(BaseModel):
    """Provider-neutral observation selected for Memory formation."""

    content: str = Field(min_length=1)
    event_type: MemoryEventType = MemoryEventType.USER_MEMORY
    source: SourceType = SourceType.DISTILLED
    importance: float = Field(default=1.0, ge=0.0, le=1.0)
    source_id: str | None = None
    object_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class MemoryExtractionPort(Protocol):
    """Extract durable Memory candidates from an immutable Session Archive."""

    async def extract(
        self,
        archive: SessionArchive,
        context: RequestContext,
    ) -> list[MemoryExtractionCandidate]: ...


class _LegacySessionPolicy(Protocol):
    def extract(
        self,
        archive: SessionArchive,
        context: RequestContext,
    ) -> list[MemoryEvent]: ...


class LegacySessionPolicyExtractionAdapter:
    """Bridge the original synchronous Session policy to the new Port."""

    def __init__(self, policy: _LegacySessionPolicy) -> None:
        self._policy = policy

    async def extract(
        self,
        archive: SessionArchive,
        context: RequestContext,
    ) -> list[MemoryExtractionCandidate]:
        events = self._policy.extract(archive, context)
        return [_candidate_from_event(event) for event in events]


def _candidate_from_event(event: MemoryEvent) -> MemoryExtractionCandidate:
    return MemoryExtractionCandidate(
        content=event.content,
        event_type=event.event_type,
        source=event.source,
        importance=event.importance,
        source_id=event.source_id,
        object_id=event.object_id,
        request_id=event.request_id,
        trace_id=event.trace_id,
        evidence_refs=list(event.evidence_refs),
        metadata=dict(event.metadata),
    )
