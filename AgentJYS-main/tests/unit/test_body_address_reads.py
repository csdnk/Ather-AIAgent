"""Real Remember/Bodies/Redis readers with isolated metadata and external I/O."""

import asyncio
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from aether_agent_memory.operate.basic.body_cache import TieredBodyCache
from aether_agent_memory.remember.basic.content import Bodies
from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.foundation import MemoryRecord
from aether_agent_memory.remember.contracts.models import MemoryRef, SourceRef
from aether_agent_memory.runtime.contracts.models import ErrorCode, Principal, Scope, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError, later, now
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.transactions import StorageTransaction
from aether_agent_memory.runtime.storage.redis_cache import RedisCache
from recall_records import InMemoryRecallRecords


@pytest.fixture
def body_case(tmp_path):
    records = InMemoryRecallRecords()
    state = SimpleNamespace(
        active=0,
        redis_calls=0,
        cold_calls=0,
        raw=b"body",
        cold=b"body",
        hot_hook=None,
        cold_hook=None,
    )

    @contextmanager
    def transaction():
        with records.transaction() as raw:
            state.active += 1
            try:
                yield StorageTransaction(raw, "body-read-test")
            finally:
                state.active -= 1

    def eval_read(script, count, *args):
        assert state.active == 0, "Redis I/O held the metadata transaction"
        assert script == RedisCache._READ, "ordinary read attempted cache admission"
        assert count == 1 and len(args) == 3
        state.redis_calls += 1
        if state.hot_hook:
            state.hot_hook()
        if isinstance(state.raw, Exception):
            raise state.raw
        return state.raw

    async def get_object(key):
        assert state.active == 0, "cold I/O held the metadata transaction"
        state.cold_calls += 1
        if state.cold_hook:
            await state.cold_hook()
        if isinstance(state.cold, Exception):
            raise state.cold
        return state.cold

    policy = RememberPolicy()
    redis = RedisCache(
        SimpleNamespace(
            connection_pool=SimpleNamespace(
                connection_kwargs={
                    "host": "test-redis",
                    "port": 6379,
                    "db": 0,
                    "socket_timeout": 0.1,
                }
            ),
            eval=eval_read,
        ),
        policy,
        namespace="body-tests",
    )
    p2 = SimpleNamespace(
        binding=lambda: {"provider": "ceph", "instance": "test"}, get_object=get_object
    )
    bodies = Bodies(tmp_path, policy, p2=p2, cache=TieredBodyCache(SimpleNamespace(), redis))
    bodies.cache_reader = redis
    bodies.remote_only = True
    reader = object.__new__(RememberPipeline)
    reader.uow = SimpleNamespace(transaction=transaction)
    reader.identity = Identity(reader.uow)
    reader.policy, reader.bodies = policy, bodies
    scope = Scope(tenant_id="tenant", application_id="app", user_id="alice", agent_id="agent")
    principal = Principal(
        principal_id="alice", home_scope=scope, permissions=("memory:read",), auth_epoch=1
    )
    ctx = TrustedContext(
        principal=principal,
        request_id="request",
        operation_id="read",
        trace_id="1" * 32,
        span_id="2" * 16,
        deadline_at=later(now(), 30),
    )
    source = SourceRef(
        source_id="source", source_version=1, content_hash=text_hash("body"), locator="source-key"
    )
    with transaction() as tx:
        tx.write(
            "identities", "alice", {"enabled": True, "principal": principal.model_dump(mode="json")}
        )
        tx.write(
            "remember_sources",
            source.source_id,
            {"valid": True, "ref": source.model_dump(mode="json")},
        )

    def add(memory_id="memory", *, hot=True):
        ref = MemoryRef(scope=scope, memory_id=memory_id, version=1)
        location = bodies.location(scope, "body")
        record = MemoryRecord(
            ref=ref,
            revision=1,
            relations_revision=1,
            kind="working",
            status="active",
            body_location=location,
            body_chars=4,
            sources=(source,),
            projection_state="pending",
            created_at=now(),
            cache_location=redis.describe_location(
                scope, text_hash("body"), generation=location.generation
            )
            if hot
            else None,
        )
        with transaction() as tx:
            target = memory_ref(ref, versioned=True)
            tx.put_if_revision(target, record.model_dump(mode="json"), None)
            tx.write("remember_current", ref.memory_id, target.model_dump(mode="json"))
        return record

    record = add()

    def change(**updates):
        with transaction() as tx:
            target = memory_ref(record.ref, versioned=True)
            raw = tx.get(target)
            tx.put_if_revision(target, {**raw, **updates}, tx.revision(target))

    return SimpleNamespace(
        reader=reader,
        ctx=ctx,
        record=record,
        ref=record.ref,
        transaction=transaction,
        state=state,
        cache=redis,
        add=add,
        change=change,
    )


async def test_hot_address_survives_cold_failure_and_returns_actual_location(body_case):
    p = body_case
    p.state.cold = OSError("cold body offline")
    result = await p.reader.read_body(p.ctx, p.ref)
    assert (result.outcome, result.content, result.path) == ("read", "body", "cache")
    assert result.location == p.record.cache_location
    assert p.state.cold_calls == 0
    assert not p.reader.bodies.verified


async def test_absent_address_skips_same_hash_cache_and_never_admits(body_case):
    p = body_case
    p.change(cache_location=None)
    with p.transaction() as tx:
        before = tx.get(memory_ref(p.ref, versioned=True))
    result = await p.reader.read_body(p.ctx, p.ref)
    assert (result.content, result.path) == ("body", "authority")
    assert (p.state.redis_calls, p.state.cold_calls) == (0, 1)
    with p.transaction() as tx:
        assert tx.get(memory_ref(p.ref, versioned=True)) == before
        assert tx.rows("remember_cache_admission") == []


@pytest.mark.parametrize("cache_value", [None, b"wrong", b"\xff", OSError("offline")])
async def test_missing_corrupt_or_unavailable_cache_falls_back_without_writes(
    body_case, cache_value
):
    p = body_case
    p.state.raw = cache_value
    result = await p.reader.read_body(p.ctx, p.ref)
    assert (result.content, result.path, result.location) == (
        "body",
        "authority",
        p.record.body_location,
    )
    with p.transaction() as tx:
        assert tx.get(memory_ref(p.ref, versioned=True)) == p.record.model_dump(mode="json")
        assert tx.rows("remember_cache_admission") == []


@pytest.mark.parametrize("address", [{"bad": "cache"}, "old-format", {"kind": "body"}])
async def test_invalid_optional_address_preserves_valid_authority(body_case, address):
    p = body_case
    p.change(cache_location=address)
    result = await p.reader.read_body(p.ctx, p.ref)
    assert (result.outcome, result.content, result.path) == ("read", "body", "authority")
    assert p.state.redis_calls == 0


async def test_placement_only_change_during_hot_read_does_not_invalidate_body(body_case):
    p = body_case
    p.state.hot_hook = lambda: p.change(cache_location=None)
    result = await p.reader.read_body(p.ctx, p.ref)
    assert result.outcome == "read" and result.location == p.record.cache_location
    with p.transaction() as tx:
        assert tx.get(memory_ref(p.ref, versioned=True))["cache_location"] is None


@pytest.mark.parametrize(
    "update", [{"relations_revision": 2}, {"object_revision": 2}, {"body_chars": 9}]
)
async def test_semantic_change_during_read_is_rejected(body_case, update):
    p = body_case
    p.state.hot_hook = lambda: p.change(**update)
    result = await p.reader.read_body(p.ctx, p.ref)
    assert result.outcome == "stale" and result.content is None


async def test_identity_revocation_during_hot_read_preserves_forbidden(body_case):
    p = body_case

    def revoke():
        with p.transaction() as tx:
            row = tx.read("identities", "alice")
            tx.write("identities", "alice", {**row, "enabled": False})

    p.state.hot_hook = revoke
    with pytest.raises(FoundationError) as failure:
        await p.reader.read_body(p.ctx, p.ref)
    assert failure.value.code == ErrorCode.FORBIDDEN


async def test_recall_batch_uses_explicit_bodies_without_legacy_hydration(body_case):
    p = body_case
    p.state.cold = OSError("cold body offline")

    def legacy(*args):
        raise AssertionError("Recall used legacy decoding/hydration")

    p.reader.load = p.reader.decode = p.reader.bodies.read_local = legacy
    batch = await p.reader.load_recall_batch(p.ctx, (p.ref,))
    assert [(item.ref, item.content) for item in batch.items] == [(p.ref, "body")]
    assert batch.eligibility.items[0].decision == "allowed"
    assert p.state.cold_calls == 0 and not p.reader.bodies.verified


async def test_inline_snapshot_requires_no_external_body(body_case):
    p = body_case
    snapshot = p.reader.authority_snapshot(p.record, "body")
    with p.transaction() as tx:
        target = memory_ref(p.ref, versioned=True)
        tx.put_if_revision(target, snapshot.model_dump(mode="json"), tx.revision(target))
    result = await p.reader.read_body(p.ctx, p.ref)
    assert result.outcome == "read" and result.content == "body"
    assert p.state.redis_calls == p.state.cold_calls == 0


async def test_inline_snapshot_with_wrong_declared_hash_is_rejected(body_case):
    p = body_case
    snapshot = p.reader.authority_snapshot(p.record, "body").model_dump(mode="json")
    snapshot["content"] = "tampered"
    with p.transaction() as tx:
        target = memory_ref(p.ref, versioned=True)
        tx.put_if_revision(target, snapshot, tx.revision(target))
    with pytest.raises(FoundationError) as failure:
        await p.reader.read_body(p.ctx, p.ref)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION


async def test_slow_cache_leaves_deadline_budget_for_authority(body_case):
    p = body_case

    class SlowCache:
        read_timeout_seconds = 0.01

        async def read_location(self, *args):
            await asyncio.sleep(1)

    p.reader.bodies.cache_reader = SlowCache()
    ctx = p.ctx.model_copy(update={"deadline_at": later(now(), 0.3)})
    result = await p.reader.read_body(ctx, p.ref)
    assert result.content == "body" and result.path == "authority"


async def test_batch_excludes_out_of_scope_candidate_without_aborting_valid_reads(body_case):
    p = body_case
    foreign = p.ref.model_copy(
        update={"scope": p.ref.scope.model_copy(update={"tenant_id": "foreign"})}
    )
    batch = await p.reader.load_recall_batch(p.ctx, (foreign, p.ref))
    assert [e.decision for e in batch.eligibility.items] == ["excluded", "allowed"]
    assert [item.ref for item in batch.items] == [p.ref]


async def test_both_layers_unavailable_produces_unverifiable_batch(body_case):
    p = body_case
    p.state.raw, p.state.cold = OSError("hot offline"), OSError("cold offline")
    batch = await p.reader.load_recall_batch(p.ctx, (p.ref,))
    assert batch.items == ()
    assert batch.eligibility.items[0].decision == "unverifiable"


@pytest.mark.parametrize("change", ["delete", "source"])
async def test_withdrawal_during_hot_io_never_returns_body(body_case, change):
    p = body_case

    def withdraw():
        if change == "delete":
            p.change(status="deleted")
        else:
            with p.transaction() as tx:
                tx.write("remember_sources", "source", {"valid": False})

    p.state.hot_hook = withdraw
    result = await p.reader.read_body(p.ctx, p.ref)
    assert result.outcome == "stale" and result.content is None


@pytest.mark.parametrize(
    "updates",
    [
        {"body_chars": -1},
        {"extra_authority": "invalid"},
        {"ref": {"scope": {"tenant_id": "foreign"}}},
    ],
)
async def test_invalid_authority_is_typed_failure_before_any_io(body_case, updates):
    p = body_case
    p.change(cache_location={"broken": True}, **updates)
    with pytest.raises(FoundationError) as failure:
        await p.reader.read_body(p.ctx, p.ref)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert p.state.redis_calls == p.state.cold_calls == 0


async def test_valid_but_wrong_record_ref_never_reads_body(body_case):
    p = body_case
    other = p.ref.model_copy(
        update={"scope": p.ref.scope.model_copy(update={"tenant_id": "foreign"})}
    )
    p.change(ref=other.model_dump(mode="json"))
    with pytest.raises(FoundationError) as failure:
        await p.reader.read_body(p.ctx, p.ref)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert p.state.redis_calls == p.state.cold_calls == 0


async def test_deadline_includes_authority_fallback(body_case):
    p = body_case
    p.state.raw = None

    async def delay():
        await asyncio.sleep(1)

    p.state.cold_hook = delay
    ctx = p.ctx.model_copy(update={"deadline_at": later(now(), 0.05)})
    with pytest.raises(FoundationError) as failure:
        await p.reader.read_body(ctx, p.ref)
    assert failure.value.code == ErrorCode.DEADLINE_EXCEEDED


async def test_batch_deadline_also_bounds_final_metadata_and_conflict_assembly(body_case):
    p = body_case

    def delayed_conflicts(*args):
        time.sleep(0.5)
        return ()

    p.reader.conflict_groups_in = delayed_conflicts
    ctx = p.ctx.model_copy(update={"deadline_at": later(now(), 0.1)})
    started = time.monotonic()
    with pytest.raises(FoundationError) as failure:
        await p.reader.load_recall_batch(ctx, (p.ref,))
    elapsed = time.monotonic() - started
    assert failure.value.code == ErrorCode.DEADLINE_EXCEEDED
    assert elapsed < 0.35


@pytest.mark.parametrize("code", [ErrorCode.FORBIDDEN, ErrorCode.DEADLINE_EXCEEDED])
async def test_cache_authorization_and_deadline_errors_are_not_swallowed(body_case, code):
    p = body_case
    p.state.raw = FoundationError(code, "typed read failure")
    with pytest.raises(FoundationError) as failure:
        await p.reader.read_body(p.ctx, p.ref)
    assert failure.value.code == code
    assert p.state.cold_calls == 0


async def test_corrupt_cold_body_is_rejected(body_case):
    p = body_case
    p.state.raw, p.state.cold = None, b"corrupt"
    with pytest.raises(FoundationError) as failure:
        await p.reader.read_body(p.ctx, p.ref)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION


async def test_batch_accepts_malformed_optional_cache_without_revalidating_it_as_authority(
    body_case,
):
    p = body_case
    p.change(cache_location={"old": "bad-address"})
    batch = await p.reader.load_recall_batch(p.ctx, (p.ref,))
    assert [item.content for item in batch.items] == ["body"]
    assert p.state.redis_calls == 0


@pytest.mark.parametrize(
    "binding", ["tenant", "instance", "namespace", "generation", "hash", "encoding"]
)
async def test_wrong_cache_binding_falls_back_without_reading_target(body_case, binding):
    p = body_case
    location = p.record.cache_location
    if binding == "tenant":
        location = p.cache.describe_location(
            p.ref.scope.model_copy(update={"tenant_id": "other"}),
            p.record.body_location.content_hash,
            generation=p.record.body_location.generation,
        )
    else:
        changes = {
            "instance": {"provider_instance_id": "other"},
            "namespace": {"namespace": "other"},
            "generation": {"generation": "old"},
            "hash": {"content_hash": text_hash("other")},
            "encoding": {"object_key": "old/bucket/key"},
        }
        location = location.model_copy(update=changes[binding])
    p.change(cache_location=location.model_dump(mode="json"))
    result = await p.reader.read_body(p.ctx, p.ref)
    assert result.content == "body" and result.path == "authority"
    assert p.state.redis_calls == 0


def test_azure_rejects_injected_cache_reader_before_acquiring_providers(tmp_path, monkeypatch):
    from aether_agent_memory.runtime.flows.application import Service
    from aether_agent_memory.runtime.storage import azure

    def forbidden_provider(*args, **kwargs):
        raise AssertionError("Azure started acquiring providers before rejecting injected reader")

    monkeypatch.setattr(azure, "StorageProviders", forbidden_provider)
    service = object.__new__(Service)
    service.directory_lock = SimpleNamespace(acquire=lambda path: None)
    config = SimpleNamespace(
        storage_mode="azure",
        data_dir=tmp_path,
        remember=RememberPolicy(),
        log_retention_days=14,
        log_max_records=1000,
        azure_storage=SimpleNamespace(
            require_credentials=lambda: None, postgres=SimpleNamespace(resolve_dsn=lambda: "unused")
        ),
    )
    with pytest.raises(ValueError, match="cannot be mixed"):
        service.initialize(config, cache_reader=SimpleNamespace())
