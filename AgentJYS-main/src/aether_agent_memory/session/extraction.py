from __future__ import annotations

from typing import Protocol

from aether_agent_memory.b2 import MemoryEvent, MemoryEventType
from aether_agent_memory.context_store.mapping import session_archive_uri
from aether_agent_memory.core.enums import SourceType
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.session.models import SessionArchive


class SessionMemoryExtractionPolicy(Protocol):
    """Provider-neutral policy turning an immutable archive into observations."""

    def extract(
        self,
        archive: SessionArchive,
        context: RequestContext,
    ) -> list[MemoryEvent]: ...


class DeterministicSessionMemoryExtractionPolicy:
    """Compatibility extractor used until a semantic provider is configured."""

    algorithm = "session-archive-deterministic-v1"

    def extract(
        self,
        archive: SessionArchive,
        context: RequestContext,
    ) -> list[MemoryEvent]:
        archive_uri = session_archive_uri(context.scope, archive.archive_id)
        return [
            MemoryEvent(
                event_type=MemoryEventType.USER_MEMORY,
                session_id=context.session_id or "",
                agent_id=context.agent_id or "",
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                request_id=f"session-consolidation:{archive.archive_id}",
                trace_id=f"session-consolidation:{archive.archive_id}",
                source=SourceType.DISTILLED,
                source_id=str(archive_uri),
                object_id=archive.archive_id,
                content=archive.abstract,
                evidence_refs=[str(archive_uri)],
                metadata={
                    "formation_source": "session_archive",
                    "session_archive_id": archive.archive_id,
                    "session_archive_uri": str(archive_uri),
                    "overview": archive.overview,
                    "overview_generator": self.algorithm,
                    "extraction_policy": self.algorithm,
                },
            )
        ]
