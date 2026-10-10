"""Control lifecycle checks; these are not native-chain acceptance evidence."""

import asyncio
from types import SimpleNamespace

import pytest

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from recall_projection_state_support import ProjectionControl, assert_state_observation


def setup_control(mode):
    calls = []

    async def publish(ctx, task, item, prepared, *, reconcile=False):
        calls.append((item, prepared, reconcile))
        return "real delegate"

    owner = SimpleNamespace(publish_projection=publish)
    scope = SimpleNamespace(session_id="owned")
    ref = SimpleNamespace(scope=scope, model_dump=lambda **kw: {"memory_id": "original"})
    item = SimpleNamespace(kind="working", ref=ref)
    ctx = SimpleNamespace(operation_id="operation", trace_id="a" * 32)
    task = SimpleNamespace(task_id="original-job")
    return ProjectionControl(SimpleNamespace(remember=owner), "owned", mode), item, ctx, task, calls


async def test_pending_barrier_releases_original_call_and_restores_after_assertion(monkeypatch):
    control, item, ctx, task, calls = setup_control("pending")
    owner = control.runtime.remember
    original = owner.publish_projection
    pending = None
    try:
        with (
            pytest.raises(AssertionError, match="test interruption"),
            control.installed(monkeypatch),
        ):
            pending = asyncio.create_task(owner.publish_projection(ctx, task, item, {}))
            async with asyncio.timeout(2):
                while not control.entered.is_set():
                    await asyncio.sleep(0)
            assert not pending.done() and not calls
            assert control.calls[0]["projection_job_id"] == task.task_id
            raise AssertionError("test interruption")
        assert control.released.is_set() and owner.publish_projection is original
        assert await asyncio.wait_for(pending, 2) == "real delegate"
        assert len(calls) == 1 and control.calls[0]["delegated"]
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_failure_injection_is_repeatable_session_scoped_and_restored(monkeypatch):
    control, item, ctx, task, calls = setup_control("failed")
    owner = control.runtime.remember
    original = owner.publish_projection
    with control.installed(monkeypatch):
        for _ in range(2):
            with pytest.raises(FoundationError) as failure:
                await owner.publish_projection(ctx, task, item, {})
            assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
        assert not calls and len(control.calls) == 2
        item.ref.scope.session_id = "unrelated"
        assert (
            await owner.publish_projection(ctx, task, item, {}, reconcile=True) == "real delegate"
        )
        assert len(calls) == 1 and calls[0][2] is True
    assert owner.publish_projection is original and control.released.is_set()
    item.ref.scope.session_id = "owned"
    assert await owner.publish_projection(ctx, task, item, {}) == "real delegate"


@pytest.mark.parametrize("fault", ["empty", "wrong-state", "body-fallback", "other-source"])
def test_state_assertions_reject_false_success_or_source_substitution(fault):
    observation = {
        "readiness": [
            {
                "source": "working",
                "ready_count": 0,
                "pending_count": 1,
                "failed_count": 0,
                "complete": False,
            }
        ],
        "body_read_calls": 0,
        "routes": [{"source": "working"}],
    }
    if fault == "empty":
        observation["readiness"][0].update(complete=True, pending_count=0)
    elif fault == "wrong-state":
        observation["readiness"][0].update(pending_count=0, failed_count=1)
    elif fault == "body-fallback":
        observation["body_read_calls"] = 1
    else:
        observation["routes"][0]["source"] = "long_term"
    with pytest.raises(AssertionError):
        assert_state_observation(observation, "pending")
