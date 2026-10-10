"""AET-43 repeatable barriers at candidate and real SDK boundaries."""

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from hashlib import sha256
from threading import Event
from time import monotonic

from recall_both_sources_support import SOURCES, FixedCandidateRoutes


def pack_hash(payload):
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class OrderedRoutes(FixedCandidateRoutes):
    def __init__(self, working, long_term):
        super().__init__(working, long_term)
        self.entered = {s: asyncio.Event() for s in SOURCES}
        self.release = {s: asyncio.Event() for s in SOURCES}
        self.completed = {s: asyncio.Event() for s in SOURCES}
        self.timeline = []

    async def search(self, ctx, request):
        source = request.memory_source
        self.timeline.append({"source": source, "stage": "entered", "monotonic": monotonic()})
        self.entered[source].set()
        await self.release[source].wait()
        result = await super().search(ctx, request)
        self.timeline.append({"source": source, "stage": "completed", "monotonic": monotonic()})
        self.completed[source].set()
        return result


class LateSDK:
    """Short transport deadline around a real call; SDK returns after cancellation.

    This test-owned transport budget is separate from the request deadline. No
    candidate/result is fabricated. The SDK delegates first and withholds its real
    reply; only the chosen session/source is delayed. Cleanup always releases it.
    """

    def __init__(self, runtime, session, source):
        self.runtime, self.session, self.source = runtime, session, source
        self.entered, self.release, self.returned = Event(), Event(), Event()
        self.timeline = []
        self.settled = Event()
        self.tracked_future = False

    def stamp(self, stage, **values):
        self.timeline.append(
            {
                "stage": stage,
                "monotonic": monotonic(),
                "utc": datetime.now(UTC).isoformat(),
                **values,
            }
        )

    @contextmanager
    def installed(self, monkeypatch):
        vectors = self.runtime.vectors
        search, sdk = vectors.search, vectors.client.search
        active = ContextVar("aet43_late_sdk", default=None)

        async def route(ctx, request):
            if request.memory_source != self.source or request.selection.session_id != self.session:
                return await search(ctx, request)
            token = active.set((ctx, request))
            started = asyncio.Event()
            loop = asyncio.get_running_loop()
            self.signal = lambda: loop.call_soon_threadsafe(started.set)
            task = asyncio.create_task(search(ctx, request))
            try:
                await asyncio.wait_for(started.wait(), timeout=10)
                self.stamp(
                    "transport_deadline_armed",
                    operation_id=ctx.operation_id,
                    request_deadline=request.deadline_at,
                    budget_seconds=0.05,
                )
                try:
                    return await asyncio.wait_for(task, timeout=0.05)
                except TimeoutError:
                    self.stamp("transport_deadline_exceeded", reason="source_transport_timeout")
                    raise
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                active.reset(token)

        def sdk_search(*args, **kwargs):
            current = active.get()
            result = sdk(*args, **kwargs)
            if current is not None:
                ctx, request = current
                self.stamp(
                    "sdk_reply_held",
                    operation_id=ctx.operation_id,
                    source=request.memory_source,
                    hit_count=sum(len(hits) for hits in result),
                )
                self.entered.set()
                self.signal()
                try:
                    assert self.release.wait(15), "test failed to release delayed SDK reply"
                    self.stamp("late_sdk_return")
                finally:
                    self.returned.set()
            return result

        def track_submit(submit, *args, **kwargs):
            future = submit(*args, **kwargs)
            if active.get() is not None:
                self.tracked_future = True

                def settled(_):
                    self.stamp("late_future_completed")
                    self.settled.set()

                future.add_done_callback(settled)
            return future

        with monkeypatch.context() as patch:
            if hasattr(vectors, "executor"):
                submit = vectors.executor.submit
                patch.setattr(
                    vectors.executor, "submit", lambda *a, **k: track_submit(submit, *a, **k)
                )
            patch.setattr(vectors, "search", route)
            patch.setattr(vectors.client, "search", sdk_search)
            try:
                yield self
            finally:
                self.release.set()
                if self.entered.is_set():
                    assert self.returned.wait(5), "late SDK worker did not terminate"
                if self.tracked_future:
                    assert self.settled.wait(5), "late SDK future did not settle"
