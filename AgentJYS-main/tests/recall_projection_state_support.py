"""AET-38 controls and observations, scoped to an owned session.

The barrier runs after real passage encoding and before projection publication.
No metadata is edited and no Recall result, readiness or vector hit is fabricated.
"""

import asyncio
from contextlib import contextmanager
from threading import Event

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError


class ProjectionControl:
    def __init__(self, runtime, session, mode):
        assert mode in {"pending", "failed"}
        self.runtime, self.session, self.mode = runtime, session, mode
        self.entered, self.released = Event(), Event()
        self.calls = []

    @contextmanager
    def installed(self, monkeypatch):
        owner = self.runtime.remember
        original = owner.publish_projection

        async def controlled(ctx, task, item, prepared, *, reconcile=False):
            if item.kind != "working" or item.ref.scope.session_id != self.session:
                return await original(ctx, task, item, prepared, reconcile=reconcile)
            row = {
                "projection_job_id": task.task_id,
                "operation_id": ctx.operation_id,
                "trace_id": ctx.trace_id,
                "memory": item.ref.model_dump(mode="json"),
                "mode": self.mode,
                "stage": "publish_projection",
                "error_code": None,
                "delegated": False,
            }
            self.calls.append(row)
            self.entered.set()
            if self.mode == "failed" and not self.released.is_set():
                row["error_code"] = ErrorCode.CONTRACT_VIOLATION.value
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "AET-38 owned projection publication failure"
                )
            while not self.released.is_set():
                await asyncio.sleep(0.02)
            row["delegated"] = True
            return await original(ctx, task, item, prepared, reconcile=reconcile)

        with monkeypatch.context() as patch:
            patch.setattr(owner, "publish_projection", controlled)
            try:
                yield self
            finally:
                # Release before restoring the method, including assertion/cancellation paths.
                self.released.set()


class ProjectionStateProbe:
    """Forward real public port calls, keeping only metadata for evidence."""

    def __init__(self, runtime, monkeypatch):
        self.readiness, self.body_reads = [], []
        memories = runtime.recall.memories
        original = memories.projection_readiness

        async def readiness(ctx, selection, source):
            result = await original(ctx, selection, source)
            self.readiness.append(
                {
                    "operation_id": ctx.operation_id,
                    "trace_id": ctx.trace_id,
                    **result.model_dump(mode="json"),
                }
            )
            return result

        monkeypatch.setattr(memories, "projection_readiness", readiness)
        bodies = runtime.recall.assembly.bodies
        load = bodies.load_bodies

        async def load_bodies(ctx, request):
            self.body_reads.append(ctx.operation_id)
            return await load(ctx, request)

        monkeypatch.setattr(bodies, "load_bodies", load_bodies)

    def observe(self, operation_id, vector_probe):
        ready = [r for r in self.readiness if r["operation_id"] == operation_id]
        searches = [r for r in vector_probe.searches if r["operation_id"] == operation_id]
        return {
            "readiness": ready,
            "routes": [
                {
                    "source": r["request"].memory_source,
                    "model_space": r["request"].model_space,
                    "sdk_calls": len(r["sdk"]),
                    "coverage": r["result"].coverage if r["result"] is not None else None,
                }
                for r in searches
            ],
            "body_read_calls": self.body_reads.count(operation_id),
        }


def assert_not_success(response):
    assert response.status_code != 200, "unready index was reported as Recall success"
    value = response.json()
    assert not {"groups", "rendered_context", "pack", "items"}.intersection(value), (
        "index failure was hidden by a body/lexical fallback"
    )
    return value


def assert_state_observation(observation, state):
    rows = observation["readiness"]
    assert rows, "Recall never inspected real projection readiness"
    assert all(r["source"] == "working" for r in rows)
    for row in rows:
        assert row["ready_count"] == 0 and not row["complete"]
        assert row["pending_count"] == (1 if state == "pending" else 0)
        assert row["failed_count"] == (1 if state == "failed" else 0)
    assert observation["body_read_calls"] == 0, "Recall enumerated authoritative bodies"
    assert all(r["source"] == "working" for r in observation["routes"])
