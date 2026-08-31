from __future__ import annotations

from pydantic import BaseModel, Field

from aether_agent_memory.context_store.errors import ContextProjectionSupersededError
from aether_agent_memory.context_store.models import (
    ContextProjectionWorkItem,
    ContextProjectionWorkStatus,
)
from aether_agent_memory.context_store.ports import (
    ContextProjectionExecutorPort,
    ContextProjectionQueuePort,
)


class ContextProjectionWorkerReport(BaseModel):
    requested: int = Field(ge=0)
    claimed: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    retried: int = Field(default=0, ge=0)
    superseded: int = Field(default=0, ge=0)
    errors: dict[str, str] = Field(default_factory=dict)


class ContextProjectionWorkService:
    """Bounded worker for Resource/Skill/Session derived Context views."""

    def __init__(
        self,
        queue: ContextProjectionQueuePort,
        executor: ContextProjectionExecutorPort,
        *,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("context projection max_attempts must be positive")
        self._queue = queue
        self._executor = executor
        self._max_attempts = max_attempts

    async def enqueue(self, item: ContextProjectionWorkItem) -> ContextProjectionWorkItem:
        return await self._queue.enqueue(item)

    async def drain(self, *, limit: int = 100) -> ContextProjectionWorkerReport:
        if limit < 1:
            raise ValueError("context projection worker limit must be positive")
        pending = (await self._queue.pending())[:limit]
        report = ContextProjectionWorkerReport(
            requested=len(pending), claimed=0, succeeded=0, failed=0, skipped=0
        )
        for item in pending:
            claimed = await self._queue.claim(item.work_id)
            if claimed is None:
                report.skipped += 1
                continue
            report.claimed += 1
            try:
                await self._executor.execute(claimed)
            except ContextProjectionSupersededError:
                if await self._queue.supersede(claimed.work_id) is not None:
                    report.superseded += 1
                else:
                    report.skipped += 1
                continue
            except Exception as exc:
                report.failed += 1
                report.errors[claimed.work_id] = f"{type(exc).__name__}: {exc}"
                failed = await self._queue.fail(
                    claimed.work_id, report.errors[claimed.work_id]
                )
                if (
                    failed is not None
                    and failed.attempts < self._max_attempts
                    and await self._queue.retry(claimed.work_id) is not None
                ):
                    report.retried += 1
                continue
            completed = await self._queue.complete(claimed.work_id)
            if completed is None or completed.status != ContextProjectionWorkStatus.SUCCEEDED:
                report.skipped += 1
                continue
            report.succeeded += 1
        return report

    async def close(self) -> None:
        await self._queue.close()


__all__ = ["ContextProjectionWorkerReport", "ContextProjectionWorkService"]
