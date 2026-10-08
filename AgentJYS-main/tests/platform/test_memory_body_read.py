"""Single-fetch full-body reads retain real metadata authorization and race guards."""

import asyncio
from hashlib import sha256
from types import SimpleNamespace

import pytest
from test_p3_admin_diagnostics import runtime, seed
from test_ruoyi_p3 import MemoryUow

from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.transactions import StorageTransaction
from aether_agent_memory.runtime.storage.redis_cache import RedisCache


@pytest.fixture
def body_world(tmp_path):
    identity = Identity(MemoryUow())
    identity.provision(
        [
            (
                sha256(b"reader").hexdigest(),
                Principal(
                    principal_id="reader",
                    home_scope=Scope(
                        tenant_id="old-tenant",
                        user_id="old-user",
                        application_id="p3",
                        agent_id="p3-agent",
                    ),
                    permissions=(Permission.READ,),
                    auth_epoch=1,
                ),
            )
        ]
    )
    host = runtime(identity, tmp_path)
    item = seed(host, text="immutable complete body")
    return host, identity.context("reader"), item


def count_fetches(monkeypatch, bodies, after=None):
    reads = []
    original = bodies.read_authority

    async def read(location):
        reads.append(location)
        result = await original(location)
        if after is not None:
            after()
        return result

    monkeypatch.setattr(bodies, "read_authority", read)
    if bodies.cache_reader is not None:
        original_cache = bodies.cache_reader.read_location

        async def read_cache(scope, location, authority):
            reads.append(location)
            result = await original_cache(scope, location, authority)
            if after is not None:
                after()
            return result

        monkeypatch.setattr(bodies.cache_reader, "read_location", read_cache)
    return reads


def register_cache(host, item, content):
    """Exercise the real address validator/decoder; only Redis I/O is substituted."""

    def eval_read(script, key_count, *args):
        assert script == RedisCache._READ and key_count == 1 and len(args) == 3
        return content.encode("utf-8")

    cache = RedisCache(
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
        host.remember.policy,
        namespace="platform-body",
    )
    with host.foundation.uow.transaction() as tx:
        ref = memory_ref(item.ref, versioned=True)
        raw = tx.get(ref)
        authority = ResourceLocation.model_validate(raw["body_location"])
        location = cache.describe_location(
            item.ref.scope, authority.content_hash, generation=authority.generation
        )
        tx.put_if_revision(
            ref, {**raw, "cache_location": location.model_dump(mode="json")}, tx.revision(ref)
        )
    host.remember.bodies.cache_reader = cache
    return location


@pytest.mark.parametrize("cached", [False, True])
def test_body_fetches_once_and_keeps_actual_path(body_world, monkeypatch, cached):
    host, ctx, item = body_world

    def no_decode(*args):
        pytest.fail("full-body read decoded plaintext inside a metadata transaction")

    monkeypatch.setattr(host.remember, "decode", no_decode)
    if cached:
        location = register_cache(host, item, item.content)
    reads = count_fetches(monkeypatch, host.remember.bodies)
    result = asyncio.run(host.remember.read_body(ctx, item.ref))
    assert result.content == "immutable complete body"
    assert result.path == ("cache" if cached else "authority")
    assert result.guard.object_revision == 1
    assert len(reads) == 1
    if cached:
        assert result.location == location


def test_body_uses_stored_immutable_address(body_world):
    host, ctx, item = body_world
    with host.foundation.uow.transaction() as tx:
        ref = memory_ref(item.ref, versioned=True)
        raw = tx.get(ref)
        location = ResourceLocation.model_validate(raw["body_location"]).model_copy(
            update={"object_key": "remember/bodies/migrated-immutable-key"}
        )
        host.remember.bodies._spool(location, item.content)
        tx.put_if_revision(
            ref, {**raw, "body_location": location.model_dump(mode="json")}, tx.revision(ref)
        )
    result = asyncio.run(host.remember.read_body(ctx, item.ref))
    assert result.content == "immutable complete body"
    assert result.location == location


def test_corrupt_cache_falls_back_to_verified_authority(body_world, monkeypatch):
    host, ctx, item = body_world
    cache_location = register_cache(host, item, "corrupt cache entry")
    reads = count_fetches(monkeypatch, host.remember.bodies)
    result = asyncio.run(host.remember.read_body(ctx, item.ref))
    assert result.content == "immutable complete body" and result.path == "authority"
    assert reads == [cache_location, result.location]


def test_corrupt_authority_never_returns_plaintext(body_world):
    host, ctx, item = body_world
    location = host.remember.bodies.location(item.ref.scope, item.content)
    host.remember.bodies.path(location).write_text("corrupt authority body", encoding="utf-8")
    with pytest.raises(FoundationError) as error:
        asyncio.run(host.remember.read_body(ctx, item.ref))
    assert error.value.code == "CONTRACT_VIOLATION"


@pytest.mark.parametrize("change", ["source", "version", "object", "evidence"])
def test_body_changed_during_fetch_is_stale_without_content(body_world, monkeypatch, change):
    host, ctx, item = body_world

    def mutate():
        with host.foundation.uow.transaction() as tx:
            if change == "source":
                key = item.sources[0].source_id
                raw = tx.read("remember_sources", key)
                tx.write("remember_sources", key, {**raw, "valid": False})
            elif change == "evidence":
                tx.write(
                    "remember_relations", item.ref.memory_id, {"evidence_state": "unsupported"}
                )
            elif change == "version":
                host.remember.put(
                    tx, item.model_copy(update={"ref": item.ref.model_copy(update={"version": 2})})
                )
            else:
                host.remember.put(tx, item.model_copy(update={"object_revision": 2}))

    reads = count_fetches(monkeypatch, host.remember.bodies, mutate)
    result = asyncio.run(host.remember.read_body(ctx, item.ref))
    assert result.outcome == "stale"
    assert result.content is None and result.guard is None and result.path == "none"
    assert result.reason_code == "changed_during_read"
    assert len(reads) == 1


def test_excluded_body_does_not_fetch(body_world, monkeypatch):
    host, ctx, item = body_world
    with host.foundation.uow.transaction() as tx:
        key = item.sources[0].source_id
        raw = tx.read("remember_sources", key)
        tx.write("remember_sources", key, {**raw, "valid": False})
    reads = count_fetches(monkeypatch, host.remember.bodies)
    result = asyncio.run(host.remember.read_body(ctx, item.ref))
    assert result.outcome == "excluded" and result.reason_code == "source_deleted"
    assert result.content is None and reads == []


@pytest.mark.parametrize("during_read", [False, True])
def test_revoked_identity_never_returns_body(body_world, monkeypatch, during_read):
    host, ctx, item = body_world

    def revoke():
        with host.foundation.uow.transaction() as tx:
            raw = tx.read("identities", "reader")
            tx.write("identities", "reader", {**raw, "enabled": False})

    reads = count_fetches(monkeypatch, host.remember.bodies, revoke if during_read else None)
    if not during_read:
        revoke()
    with pytest.raises(FoundationError):
        asyncio.run(host.remember.read_body(ctx, item.ref))
    assert len(reads) == int(during_read)


def test_unavailable_body_preserves_error(body_world, monkeypatch):
    host, ctx, item = body_world
    with host.foundation.uow.transaction() as tx:
        raw = tx.get(memory_ref(item.ref, versioned=True))
    location = host.remember.bodies.location(item.ref.scope, item.content)
    assert raw["body_location"] == location.model_dump(mode="json")
    host.remember.bodies.path(location).unlink()
    with pytest.raises(FoundationError) as error:
        asyncio.run(host.remember.read_body(ctx, item.ref))
    assert error.value.code == "DEPENDENCY_UNAVAILABLE"


def test_processing_targeted_lookup_retains_batch_history_and_recursive_cycles(
    body_world, monkeypatch
):
    host, ctx, item = body_world
    with host.foundation.uow.transaction() as tx:
        for key, subject, state, child in (
            ("old_failure", "m1", "failed", None),
            ("batch", "other_parent", "succeeded", "child"),
            ("child_task", "child", "succeeded", "grandchild"),
            ("cycle", "grandchild", "succeeded", "m1"),
            ("irrelevant", "unrelated", "failed", "unrelated_child"),
        ):
            result_ref = None
            if child:
                result_ref = memory_ref(item.ref).model_copy(
                    update={"object_type": "result", "object_id": key}
                )
                tx.put_if_revision(result_ref, {"memories": [{"memory_id": child}]}, None)
            tx.write(
                "tasks",
                key,
                {
                    "record": {
                        "task_id": key,
                        "subject": {"object_id": subject},
                        "kind": "remember.extract",
                        "state": state,
                        "result_ref": result_ref.model_dump(mode="json") if result_ref else None,
                    },
                    "terminal_reason": "EXTRACTION_FAILED" if state == "failed" else None,
                },
            )
        tx.write(
            "remember_batches",
            "batch",
            {"refs": [{"memory_id": "m1"}, {"memory_id": "other_parent"}]},
        )
    baseline = host.remember.processing(ctx, "m1")
    original_rows = StorageTransaction.rows
    frontiers = []

    def targeted(tx, memory_ids):
        frontiers.append(set(memory_ids))
        batches = dict(original_rows(tx, "remember_batches"))
        return [
            (key, value)
            for key, value in original_rows(tx, "tasks")
            if value["record"]["subject"]["object_id"] in memory_ids
            or any(ref["memory_id"] in memory_ids for ref in batches.get(key, {}).get("refs", []))
        ]

    def guarded_rows(tx, table):
        assert table not in {"tasks", "remember_batches"}, (
            "processing fetched unrelated task history"
        )
        return original_rows(tx, table)

    monkeypatch.setattr(
        StorageTransaction, "processing_tasks_for_memories", targeted, raising=False
    )
    monkeypatch.setattr(StorageTransaction, "rows", guarded_rows)
    result = host.remember.processing(ctx, "m1")
    assert {**result, "tasks": sorted(result["tasks"], key=lambda row: row["task_id"])} == {
        **baseline,
        "tasks": sorted(baseline["tasks"], key=lambda row: row["task_id"]),
    }
    assert result["derived_memory_ids"] == ["child", "grandchild"]
    assert result["historical_failed_tasks"] == 1
    assert {t["task_id"] for t in result["tasks"]} == {
        "old_failure",
        "batch",
        "child_task",
        "cycle",
    }
    assert frontiers == [{"m1"}, {"child"}, {"grandchild"}]
