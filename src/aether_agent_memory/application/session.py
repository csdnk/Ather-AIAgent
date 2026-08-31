from __future__ import annotations

from contextlib import suppress

from pydantic import BaseModel, Field

from aether_agent_memory.application.scope import require_session_scope
from aether_agent_memory.application.services import WriteMemoryUseCase
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.memory.formation import (
    LegacySessionPolicyExtractionAdapter,
    MemoryExtractionCandidate,
    MemoryExtractionPort,
)
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.session.extraction import (
    DeterministicSessionMemoryExtractionPolicy,
    SessionMemoryExtractionPolicy,
)
from aether_agent_memory.session.models import SessionConsolidationResult
from aether_agent_memory.session.ports import SessionExtractionQueuePort
from aether_agent_memory.session.service import SessionService


class ConsolidateSessionUseCase:
    """Explicitly form one semantic Memory from a committed Session Archive."""

    def __init__(
        self,
        *,
        sessions: SessionService,
        write_memory: WriteMemoryUseCase,
        extraction_policy: SessionMemoryExtractionPolicy | None = None,
        extraction_port: MemoryExtractionPort | None = None,
    ) -> None:
        if extraction_policy is not None and extraction_port is not None:
            raise ValueError("configure either extraction_policy or extraction_port")
        self._sessions = sessions
        self._write_memory = write_memory
        self._extraction_port = extraction_port or LegacySessionPolicyExtractionAdapter(
            extraction_policy or DeterministicSessionMemoryExtractionPolicy()
        )

    async def execute(
        self,
        context: RequestContext,
        *,
        archive_id: str,
    ) -> SessionConsolidationResult:
        scope = require_session_scope(context, operation="session consolidation")
        archive = await self._sessions.get_archive(scope, archive_id)
        if archive is None:
            raise ValueError(f"session archive not found: {archive_id}")
        candidates = await self._extraction_port.extract(archive, context)
        if not candidates:
            raise ValueError(f"session archive produced no memory candidates: {archive_id}")
        memories = []
        for index, candidate in enumerate(candidates):
            memories.append(
                await self._write_memory.execute(
                    _event_from_candidate(candidate, context, archive_id, index),
                    context.child(
                        idempotency_key=f"session-consolidation:{archive_id}:{index}"
                    ),
                )
            )
        await self._sessions.set_archive_extraction_status(
            scope,
            archive_id,
            "succeeded",
        )
        return SessionConsolidationResult(
            session_id=context.session_id or "",
            archive_id=archive_id,
            memory_id=memories[0].id,
            memory_ids=[memory.id for memory in memories],
            status="succeeded",
            trace_id=context.trace_id,
        )

    async def mark_archive_extraction_status(
        self,
        scope: Scope,
        archive_id: str,
        status: str,
    ) -> bool:
        return await self._sessions.set_archive_extraction_status(
            scope,
            archive_id,
            status,
        )


def _event_from_candidate(
    candidate: MemoryExtractionCandidate,
    context: RequestContext,
    archive_id: str,
    index: int,
) -> MemoryEvent:
    return MemoryEvent(
        event_type=candidate.event_type,
        session_id=context.session_id or "",
        agent_id=context.agent_id or "",
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        task_id=context.task_id,
        request_id=candidate.request_id or f"session-consolidation:{archive_id}:{index}",
        trace_id=candidate.trace_id or context.trace_id,
        source_id=candidate.source_id,
        object_id=candidate.object_id,
        source=candidate.source,
        importance=candidate.importance,
        evidence_refs=list(candidate.evidence_refs),
        content=candidate.content,
        metadata=dict(candidate.metadata),
    )

class SessionExtractionWorkerReport(BaseModel):
    requested: int = Field(ge=0)
    claimed: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    retried: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    errors: dict[str, str] = Field(default_factory=dict)


class SessionExtractionWorker:
    """Drain Archive extraction jobs outside the session write transaction."""

    def __init__(
        self,
        *,
        queue: SessionExtractionQueuePort,
        consolidate: ConsolidateSessionUseCase,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("session extraction max_attempts must be positive")
        self._queue = queue
        self._consolidate = consolidate
        self._max_attempts = max_attempts

    async def drain(self, *, limit: int = 100) -> SessionExtractionWorkerReport:
        if limit < 1:
            raise ValueError("session extraction worker limit must be positive")
        pending = (await self._queue.pending())[:limit]
        report = SessionExtractionWorkerReport(
            requested=len(pending), claimed=0, succeeded=0, failed=0
        )
        for item in pending:
            claimed = await self._queue.claim(item.work_id)
            if claimed is None:
                report.skipped += 1
                continue
            report.claimed += 1
            context = RequestContext.from_values(
                request_id=f"session-extraction:{claimed.work_id}",
                trace_id=f"session-extraction:{claimed.work_id}",
                tenant_id=claimed.scope.tenant_id,
                user_id=claimed.scope.user_id,
                agent_id=claimed.scope.agent_id,
                session_id=claimed.scope.session_id,
                task_id=claimed.scope.task_id,
                idempotency_key=f"session-consolidation:{claimed.archive_id}:0",
            )
            try:
                await self._consolidate.mark_archive_extraction_status(
                    claimed.scope,
                    claimed.archive_id,
                    "processing",
                )
                await self._consolidate.execute(context, archive_id=claimed.archive_id)
                await self._consolidate.mark_archive_extraction_status(
                    claimed.scope,
                    claimed.archive_id,
                    "succeeded",
                )
            except Exception as exc:
                report.failed += 1
                report.errors[claimed.work_id] = f"{type(exc).__name__}: {exc}"
                failed = await self._queue.fail(
                    claimed.work_id, report.errors[claimed.work_id]
                )
                with suppress(Exception):
                    await self._consolidate.mark_archive_extraction_status(
                        claimed.scope,
                        claimed.archive_id,
                        (
                            "pending"
                            if failed is not None and failed.attempts < self._max_attempts
                            else "failed"
                        ),
                    )
                if (
                    failed is not None
                    and failed.attempts < self._max_attempts
                    and await self._queue.retry(claimed.work_id) is not None
                ):
                    report.retried += 1
                continue
            if await self._queue.complete(claimed.work_id) is None:
                report.skipped += 1
                continue
            report.succeeded += 1
        return report

    async def close(self) -> None:
        await self._queue.close()
