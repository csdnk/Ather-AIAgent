from __future__ import annotations

from pydantic import BaseModel, Field

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.memory.projection import (
    MemoryProjectionReconciler,
    ProjectionExecutorPort,
    ProjectionQueuePort,
    ProjectionSupersededError,
    ProjectionWorkItem,
    ProjectionWorkStatus,
)


class ProjectionWorkerReport(BaseModel):
    requested: int = Field(ge=0)
    claimed: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    retried: int = Field(default=0, ge=0)
    superseded: int = Field(default=0, ge=0)
    errors: dict[str, str] = Field(default_factory=dict)


class ProjectionWorkService:
    """Plan, enqueue, and optionally execute derived projection work."""

    def __init__(
        self,
        reconciler: MemoryProjectionReconciler,
        queue: ProjectionQueuePort,
        executor: ProjectionExecutorPort | None = None,
        *,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("projection worker max_attempts must be positive")
        self._reconciler = reconciler
        self._queue = queue
        self._executor = executor
        self._max_attempts = max_attempts

    async def plan(
        self,
        scope: Scope,
        *,
        session_only: bool = False,
    ) -> list[ProjectionWorkItem]:
        return await self._reconciler.plan(scope, session_only=session_only)

    async def reconcile(
        self,
        scope: Scope,
        *,
        session_only: bool = False,
    ) -> list[ProjectionWorkItem]:
        planned = await self.plan(scope, session_only=session_only)
        return [await self._queue.enqueue(item) for item in planned]

    async def drain(self, *, limit: int = 100) -> ProjectionWorkerReport:
        """Run a bounded worker pass with lease-safe queue transitions."""
        if limit < 1:
            raise ValueError("projection worker limit must be positive")
        if self._executor is None:
            raise RuntimeError("projection executor is not configured")
        pending = await self._queue.pending(limit=limit)
        report = ProjectionWorkerReport(
            requested=len(pending),
            claimed=0,
            succeeded=0,
            failed=0,
            skipped=0,
        )
        for item in pending:
            claimed = await self._queue.claim(item.work_id)
            if claimed is None:
                report.skipped += 1
                continue
            report.claimed += 1
            try:
                await self._executor.execute(claimed)
            except ProjectionSupersededError:
                if (
                    await self._queue.supersede(
                        claimed.work_id, claim_token=claimed.claim_token or ""
                    )
                    is not None
                ):
                    report.superseded += 1
                else:
                    report.skipped += 1
                continue
            except Exception as exc:
                report.failed += 1
                report.errors[claimed.work_id] = f"{type(exc).__name__}: {exc}"
                failed = await self._queue.fail(
                    claimed.work_id,
                    report.errors[claimed.work_id],
                    claim_token=claimed.claim_token or "",
                )
                if failed is not None and failed.attempts < self._max_attempts:
                    retried = await self._queue.retry(claimed.work_id)
                    if retried is not None:
                        report.retried += 1
                continue
            completed = await self._queue.complete(
                claimed.work_id, claim_token=claimed.claim_token or ""
            )
            if completed is None or completed.status != ProjectionWorkStatus.SUCCEEDED:
                report.skipped += 1
                continue
            report.succeeded += 1
        return report

    async def close(self) -> None:
        await self._queue.close()
