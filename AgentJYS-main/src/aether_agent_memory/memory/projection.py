from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.runtime.ports import MemoryStorePort


class ProjectionWorkKind(StrEnum):
    EMBEDDING = "embedding"
    VECTOR_INDEX = "vector_index"
    SUMMARY = "summary"


class ProjectionWorkStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class ProjectionSupersededError(RuntimeError):
    """The queued work targets an older Memory revision."""


class ProjectionWorkItem(BaseModel):
    work_id: str = Field(default_factory=lambda: uuid4().hex)
    memory_id: str
    revision: int = Field(ge=1)
    kind: ProjectionWorkKind
    scope: Scope
    status: ProjectionWorkStatus = ProjectionWorkStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    claimed_at: datetime | None = None
    lease_until: datetime | None = None
    claim_token: str | None = None


class ProjectionQueuePort(Protocol):
    async def enqueue(self, item: ProjectionWorkItem) -> ProjectionWorkItem: ...

    async def claim(self, work_id: str) -> ProjectionWorkItem | None: ...

    async def complete(self, work_id: str, *, claim_token: str) -> ProjectionWorkItem | None: ...

    async def fail(
        self, work_id: str, error: str, *, claim_token: str
    ) -> ProjectionWorkItem | None: ...

    async def supersede(self, work_id: str, *, claim_token: str) -> ProjectionWorkItem | None: ...

    async def retry(self, work_id: str) -> ProjectionWorkItem | None: ...

    async def pending(self, *, limit: int = 100) -> list[ProjectionWorkItem]: ...

    async def close(self) -> None: ...


class ProjectionExecutorPort(Protocol):
    """Execute one derived projection without exposing provider details."""

    async def execute(self, item: ProjectionWorkItem) -> None: ...


class MemoryProjectionReconciler:
    """Plan missing derived work without mutating authoritative Memory facts."""

    def __init__(self, memory_store: MemoryStorePort) -> None:
        self._memory_store = memory_store

    async def plan(
        self,
        scope: Scope,
        *,
        session_only: bool = False,
    ) -> list[ProjectionWorkItem]:
        memories = await self._memory_store.list_scoped(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            agent_id=scope.agent_id,
            session_id=scope.session_id if session_only else None,
        )
        work: list[ProjectionWorkItem] = []
        for memory in memories:
            work.extend(_missing_work(memory))
        return work

    async def plan_for(self, memory: Memory) -> list[ProjectionWorkItem]:
        """Plan derived work for one freshly written or recovered Memory."""
        return _missing_work(memory)


def _missing_work(memory: Memory) -> list[ProjectionWorkItem]:
    scope = Scope(
        tenant_id=memory.tenant_id,
        user_id=memory.user_id,
        agent_id=memory.agent_id,
        session_id=memory.session_id,
        task_id=memory.task_id,
    )
    work: list[ProjectionWorkItem] = []
    if memory.embedding_status != "succeeded":
        work.append(_work(memory, ProjectionWorkKind.EMBEDDING, scope))
    if memory.vector_projection_status != "succeeded":
        work.append(_work(memory, ProjectionWorkKind.VECTOR_INDEX, scope))
    if memory.compression_status in {"pending", "processing", "failed"}:
        work.append(_work(memory, ProjectionWorkKind.SUMMARY, scope))
    return work


def _work(
    memory: Memory,
    kind: ProjectionWorkKind,
    scope: Scope,
) -> ProjectionWorkItem:
    return ProjectionWorkItem(
        memory_id=memory.id,
        revision=memory.revision,
        kind=kind,
        scope=scope,
    )
