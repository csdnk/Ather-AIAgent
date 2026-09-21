"""Idempotency lifecycle tests: PROCESSING/SUCCEEDED/FAILED + payload hash."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aether_agent_memory.persistence.idempotency import (
    ClaimOutcome,
    RedisIdempotencyStore,
    payload_hash,
)
from aether_agent_memory.runtime.errors import ConflictError
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.service import MemoryRuntime


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self._data.get(key)


def _store() -> RedisIdempotencyStore:
    return RedisIdempotencyStore("redis://localhost:6379/0", client=_FakeRedis())


def test_claim_then_complete_returns_cached_response() -> None:
    store = _store()
    outcome, cached = store.claim(
        key="k", tenant_id="t", op_type="memory_event", payload_hash=payload_hash({"a": 1})
    )
    assert outcome == ClaimOutcome.CLAIMED.value
    store.complete(key="k", tenant_id="t", op_type="memory_event", response={"memory_id": "m1"})

    outcome2, cached2 = store.claim(
        key="k", tenant_id="t", op_type="memory_event", payload_hash=payload_hash({"a": 1})
    )
    assert outcome2 == ClaimOutcome.CACHED.value
    assert cached2 == {"memory_id": "m1"}


def test_failed_record_allows_retry() -> None:
    store = _store()
    store.claim(key="k", tenant_id="t", op_type="memory_event", payload_hash="h1")
    store.fail(key="k", tenant_id="t", op_type="memory_event")

    outcome, cached = store.claim(
        key="k", tenant_id="t", op_type="memory_event", payload_hash="h1"
    )
    assert outcome == ClaimOutcome.CLAIMED.value
    assert cached is None


def test_processing_is_conflict() -> None:
    store = _store()
    store.claim(key="k", tenant_id="t", op_type="memory_event", payload_hash="h1")
    outcome, _ = store.claim(
        key="k", tenant_id="t", op_type="memory_event", payload_hash="h1"
    )
    assert outcome == ClaimOutcome.CONFLICT.value


def test_payload_mismatch_conflicts_after_success() -> None:
    store = _store()
    store.claim(key="k", tenant_id="t", op_type="memory_event", payload_hash="h1")
    store.complete(key="k", tenant_id="t", op_type="memory_event", response={})
    outcome, _ = store.claim(
        key="k", tenant_id="t", op_type="memory_event", payload_hash="h2"
    )
    assert outcome == ClaimOutcome.CONFLICT.value


def test_keys_namespaced_by_tenant_and_op_type() -> None:
    store = _store()
    store.claim(key="k", tenant_id="t1", op_type="memory_event", payload_hash="h1")
    outcome, _ = store.claim(
        key="k", tenant_id="t2", op_type="memory_event", payload_hash="h1"
    )
    assert outcome == ClaimOutcome.CLAIMED.value


def test_payload_hash_is_stable() -> None:
    assert payload_hash({"a": 1, "b": [2, 3]}) == payload_hash({"b": [2, 3], "a": 1})


@pytest.mark.asyncio
async def test_runtime_claim_conflict_raises() -> None:
    runtime = object.__new__(MemoryRuntime)
    runtime.dependencies = SimpleNamespace(
        idempotency_store=_store(),
    )
    ctx = RequestContext.from_values(idempotency_key="k", tenant_id="t")
    await runtime._claim_idempotency(
        ctx, op_type="memory_event", request_hash="h1"
    )
    with pytest.raises(ConflictError):
        await runtime._claim_idempotency(
            ctx, op_type="memory_event", request_hash="h1"
        )


@pytest.mark.asyncio
async def test_runtime_claim_skips_without_key_or_store() -> None:
    runtime = object.__new__(MemoryRuntime)
    runtime.dependencies = SimpleNamespace(idempotency_store=None)
    outcome, cached = await runtime._claim_idempotency(
        RequestContext.from_values(), op_type="memory_event", request_hash="h1"
    )
    assert outcome == ClaimOutcome.CLAIMED.value
    assert cached is None
