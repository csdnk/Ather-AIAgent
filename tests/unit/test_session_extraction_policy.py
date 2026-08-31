from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aether_agent_memory.application.session import ConsolidateSessionUseCase
from aether_agent_memory.b2 import MemoryEvent, MemoryEventType
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.formation import MemoryExtractionCandidate
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.session.extraction import (
    DeterministicSessionMemoryExtractionPolicy,
)
from aether_agent_memory.session.models import SessionArchive, SessionMessage


def _context() -> RequestContext:
    return RequestContext.from_values(
        request_id="request-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )


def _archive() -> SessionArchive:
    return SessionArchive(
        archive_id="archive-1",
        messages=[
            SessionMessage(role="user", content="Keep the deployment decision."),
        ],
        abstract="Keep the deployment decision.",
        overview="user: Keep the deployment decision.",
        created_at=datetime.now(UTC),
    )


@pytest.mark.unit
def test_default_session_policy_emits_a_traceable_memory_event() -> None:
    event = DeterministicSessionMemoryExtractionPolicy().extract(
        _archive(),
        _context(),
    )[0]

    assert event.event_type == MemoryEventType.USER_MEMORY
    assert event.source == SourceType.DISTILLED
    assert event.content == "Keep the deployment decision."
    assert event.metadata["extraction_policy"] == "session-archive-deterministic-v1"
    assert event.evidence_refs[0].startswith("aether://")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consolidation_uses_injected_extraction_policy() -> None:
    archive = _archive()
    context = _context()
    scope = context.scope
    events: list[MemoryEvent] = []
    statuses: list[str] = []

    class _Sessions:
        async def get_archive(self, requested_scope, archive_id):
            assert requested_scope == scope
            assert archive_id == archive.archive_id
            return archive

        async def set_archive_extraction_status(
            self, requested_scope, archive_id, status
        ):
            assert requested_scope == scope
            assert archive_id == archive.archive_id
            statuses.append(status)
            return True

    class _Writer:
        async def execute(self, event, requested_context):
            assert requested_context.scope == scope
            events.append(event)
            return Memory(
                id=f"memory-{len(events)}",
                type=MemoryType.SEMANTIC,
                session_id=scope.session_id or "",
                agent_id=scope.agent_id or "",
                user_id=scope.user_id,
                tenant_id=scope.tenant_id,
                content=event.content,
                source=event.source,
            )

    class _Policy:
        def extract(self, requested_archive, requested_context):
            assert requested_archive is archive
            assert requested_context is context
            return [
                MemoryEvent(
                    event_type=MemoryEventType.USER_MEMORY,
                    session_id="session-1",
                    agent_id="agent-1",
                    user_id="user-1",
                    tenant_id="tenant-1",
                    content="Policy-selected fact",
                )
            ]

    result = await ConsolidateSessionUseCase(
        sessions=_Sessions(),
        write_memory=_Writer(),
        extraction_policy=_Policy(),
    ).execute(context, archive_id=archive.archive_id)

    assert result.memory_id == "memory-1"
    assert result.memory_ids == ["memory-1"]
    assert events[0].content == "Policy-selected fact"
    assert statuses == ["succeeded"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consolidation_uses_async_memory_extraction_port() -> None:
    archive = _archive()
    context = _context()
    scope = context.scope
    events: list[MemoryEvent] = []

    class _Sessions:
        async def get_archive(self, requested_scope, archive_id):
            assert requested_scope == scope
            assert archive_id == archive.archive_id
            return archive

        async def set_archive_extraction_status(
            self, requested_scope, archive_id, status
        ):
            assert requested_scope == scope
            assert archive_id == archive.archive_id
            return True

    class _Writer:
        async def execute(self, event, requested_context):
            events.append(event)
            return Memory(
                id=f"memory-{len(events)}",
                type=MemoryType.SEMANTIC,
                session_id=requested_context.session_id or "",
                agent_id=requested_context.agent_id or "",
                user_id=requested_context.user_id,
                tenant_id=requested_context.tenant_id,
                content=event.content,
                source=event.source,
            )

    class _Provider:
        async def extract(self, requested_archive, requested_context):
            assert requested_archive is archive
            assert requested_context is context
            return [
                MemoryExtractionCandidate(content="first extracted fact"),
                MemoryExtractionCandidate(
                    content="second extracted fact",
                    importance=0.7,
                    metadata={"provider": "test"},
                ),
            ]

    result = await ConsolidateSessionUseCase(
        sessions=_Sessions(),
        write_memory=_Writer(),
        extraction_port=_Provider(),
    ).execute(context, archive_id=archive.archive_id)

    assert result.memory_ids == ["memory-1", "memory-2"]
    assert [event.content for event in events] == [
        "first extracted fact",
        "second extracted fact",
    ]
    assert events[1].importance == 0.7
    assert events[1].metadata == {"provider": "test"}


@pytest.mark.unit
def test_consolidation_rejects_two_extraction_configuration_paths() -> None:
    class _Sessions:
        pass

    class _Writer:
        pass

    class _Policy:
        def extract(self, archive, context):
            return []

    class _Provider:
        async def extract(self, archive, context):
            return []

    with pytest.raises(ValueError, match="either extraction_policy or extraction_port"):
        ConsolidateSessionUseCase(
            sessions=_Sessions(),
            write_memory=_Writer(),
            extraction_policy=_Policy(),
            extraction_port=_Provider(),
        )
