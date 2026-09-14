"""Redis command semantics exercised without requiring a live Redis server."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.test_queue_reliability import item  # noqa: E402

from aether_agent_memory.adapters.redis_context_projection_queue import (
    RedisContextProjectionQueue,
)
from aether_agent_memory.adapters.redis_projection_queue import RedisProjectionQueue  # noqa: E402
from aether_agent_memory.adapters.redis_session_extraction_queue import (
    RedisSessionExtractionQueue,
)

fakeredis = pytest.importorskip("fakeredis.aioredis")


@pytest.fixture(params=["memory", "context", "session"])
async def redis_queue(request):
    classes = {
        "memory": RedisProjectionQueue,
        "context": RedisContextProjectionQueue,
        "session": RedisSessionExtractionQueue,
    }
    queue = classes[request.param]("redis://unused")
    await queue._redis.aclose()
    queue._redis = fakeredis.FakeRedis(decode_responses=True)
    yield queue, item(request.param)
    await queue.close()


async def test_redis_poll_reads_only_requested_batch_and_no_smembers(redis_queue, monkeypatch):
    queue, work = redis_queue
    # Populate more unrelated due payloads than a worker may fetch.
    for index in range(50):
        extra = work.model_copy(update={"work_id": str(index)})
        await queue._redis.set(queue._key(extra.work_id), extra.model_dump_json())
        await queue._redis.zadd(queue._due_key(), {extra.work_id: 0})
    original_mget = queue._redis.mget
    sizes = []

    async def mget(keys):
        sizes.append(len(keys))
        return await original_mget(keys)

    async def forbidden(*args, **kwargs):
        pytest.fail("unbounded set read")

    monkeypatch.setattr(queue._redis, "mget", mget)
    monkeypatch.setattr(queue._redis, "smembers", forbidden)
    assert len(await queue.pending(limit=3)) == 3
    assert sizes == [3]


async def test_redis_reclaim_rejects_old_token_and_preserves_new_owner(redis_queue):
    queue, work = redis_queue
    await queue.enqueue(work)
    first = await queue.claim(work.work_id)
    first.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    await queue._redis.set(queue._key(work.work_id), first.model_dump_json())
    await queue._redis.zadd(queue._due_key(), {work.work_id: 0})
    assert len(await queue.pending(limit=1)) == 1
    second = await queue.claim(work.work_id)
    assert second.claim_token != first.claim_token
    assert await queue.complete(work.work_id, claim_token=first.claim_token) is None
    assert await queue.fail(work.work_id, "late", claim_token=first.claim_token) is None
    assert await queue.pending(limit=1) == []
    await queue.fail(work.work_id, "retry", claim_token=second.claim_token)
    await queue.retry(work.work_id)
    assert len(await queue.pending(limit=1)) == 1
    assert await queue._redis.ttl(queue._key(work.work_id)) == -1
    third = await queue.claim(work.work_id)
    await queue.complete(work.work_id, claim_token=third.claim_token)
    assert await queue.pending(limit=1) == []


async def test_atomic_enqueue_dedupes_concurrent_writers(redis_queue):
    import asyncio

    queue, work = redis_queue
    copies = [work.model_copy(update={"work_id": str(i)}) for i in range(5)]
    results = await asyncio.gather(*(queue.enqueue(copy) for copy in copies))
    assert len({result.work_id for result in results}) == 1
    assert len(await queue.pending(limit=10)) == 1
