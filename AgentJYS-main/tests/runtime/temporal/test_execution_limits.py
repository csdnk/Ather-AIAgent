import asyncio

import pytest

from aether_agent_memory.runtime.temporal.locking import ExecutionLimits


@pytest.mark.asyncio
async def test_limits_preserve_tenant_scope_and_release_cancelled_waiters():
    limits = ExecutionLimits({"remember": 3}, per_tenant=1, per_scope=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first():
        async with limits.slot("a", "scope", "remember"):
            entered.set()
            await release.wait()

    async def waiter():
        async with limits.slot("a", "scope2", "remember"):
            pytest.fail("same tenant/class overlapped")

    work = asyncio.create_task(first())
    await entered.wait()
    waiting = asyncio.create_task(waiter())
    await asyncio.sleep(0.02)
    async with limits.slot("b", "scope_b", "remember"):
        assert not work.done()
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)
    release.set()
    await work
    async with limits.slot("a", "scope2", "remember"):
        pass
    assert limits.waiter_count == 0


@pytest.mark.asyncio
async def test_slow_model_class_does_not_block_engineering_in_same_scope():
    limits = ExecutionLimits({"model": 1, "engineering": 1})
    async with limits.slot("a", "scope", "model"):
        async with asyncio.timeout(1):
            async with limits.slot("a", "scope", "engineering"):
                pass
    assert limits.waiter_count == 0
