"""P3 vector adapter consumes the frozen P2 API and preserves scope/version evidence."""

import asyncio
import importlib
from hashlib import sha256
from types import SimpleNamespace

import pytest

from aether_agent_memory.p2.client import P2VectorHit
from aether_agent_memory.recall.contracts.models import VectorSearchRequest
from aether_agent_memory.remember.basic.projection import projection_target
from aether_agent_memory.remember.contracts.models import MemoryRef, ProjectionRequest
from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ErrorCode,
    Permission,
    Principal,
    Scope,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, later
from azure_test_runtime import Foundation


class CurrentWire:
    endpoint = "127.0.0.1:50052"
    collection = "tests"

    def __init__(self):
        self.records = {}
        self.deleted = set()
        self.fail_search = False
        self.fail_delete = False
        self.search_drops_after_reply = None

    async def upsert_vectors(self, records, *, collection=None):
        for record in records:
            self.records[record.chunk_id] = record

    async def search_vectors(self, query, *, top_k=5, collection=None):
        if self.fail_search:
            raise OSError("P2 lost response")
        hits = [
            P2VectorHit(
                id=key,
                score=sum(a * b for a, b in zip(query, record.vector, strict=True)),
                metadata=record.metadata,
            )
            for key, record in self.records.items()
            if key not in self.deleted
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.id))
        if self.search_drops_after_reply:
            self.search_drops_after_reply()
        return hits[:top_k]

    async def delete_vectors(self, ids, *, collection=None):
        self.deleted.update(ids)
        if self.fail_delete:
            raise OSError("P2 lost delete response")


@pytest.fixture
def adapter(tmp_path):
    # Import inside the test to expose the missing production behavior without a collection failure.
    cls = importlib.import_module("aether_agent_memory.p2.vectors").CurrentP2Vectors
    foundation = Foundation(tmp_path / "reference.db")
    scope = Scope(tenant_id="t1", application_id="app", user_id="alice", agent_id="agent")
    principal = Principal(
        principal_id="alice", auth_epoch=1, permissions=tuple(Permission), home_scope=scope
    )
    foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    client = CurrentWire()
    vectors = cls(foundation.uow, foundation.identity, "model-v1", 2, client, max_hits=2)
    ctx = foundation.identity.context("alice", timeout_seconds=30)
    target = projection_target(
        MemoryRef(scope=scope, memory_id="m1", version=1),
        sha256(b"body").hexdigest(),
        "model-v1",
        generation="generation-1",
        body_hash=sha256(b"body").hexdigest(),
    )
    yield vectors, client, foundation, ctx, target
    foundation.close()


async def test_project_uses_p2_and_inspection_checks_exact_payload(adapter):
    vectors, wire, _, ctx, target = adapter
    result = await vectors.project(
        ctx,
        ProjectionRequest(
            operation_id="op1", target=target, vector=(1, 0), deadline_at=ctx.deadline_at
        ),
    )
    assert result.state == "verified" and result.payload_matches and result.searchable
    wire.records[target.vector_id].metadata["p3_projection"]["vector_hash"] = "0" * 64
    with pytest.raises(FoundationError) as error:
        await vectors.inspect(ctx, target, "op1")
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


async def test_scope_and_model_filtering_is_partial_when_wire_bound_is_reached(adapter):
    from aether_agent_memory.recall.contracts.models import VectorSearchRequest

    vectors, wire, _, ctx, target = adapter
    await vectors.project(
        ctx,
        ProjectionRequest(
            operation_id="op1", target=target, vector=(1, 0), deadline_at=ctx.deadline_at
        ),
    )
    outsider = target.model_copy(
        update={
            "memory": MemoryRef(
                scope=Scope(tenant_id="t2", application_id="app", user_id="bob", agent_id="agent"),
                memory_id="outside",
                version=1,
            ),
            "vector_id": "b" * 64,
        }
    )
    wire.records[outsider.vector_id] = SimpleNamespace(
        vector=[2, 0],
        metadata={
            "p3_projection": {
                "target": outsider.model_dump(mode="json"),
                "vector_hash": "c" * 64,
                "schema": "p3_current_p2_v1",
            }
        },
    )
    found = await vectors.search(
        ctx,
        VectorSearchRequest(
            selection={},
            vector=(1, 0),
            model_space="model-v1",
            limit=2,
            deadline_at=ctx.deadline_at,
        ),
    )
    assert [candidate.target for candidate in found.candidates] == [target]
    assert found.coverage == "partial"


async def test_lost_projection_response_keeps_original_operation_queryable(adapter):
    vectors, wire, _, ctx, target = adapter
    wire.fail_search = True
    result = await vectors.project(
        ctx,
        ProjectionRequest(
            operation_id="op1", target=target, vector=(1, 0), deadline_at=ctx.deadline_at
        ),
    )
    assert result.state == "unknown"
    wire.fail_search = False
    checked = await vectors.inspect(ctx, target, "op1")
    assert checked.state == "verified"
    with vectors.uow.transaction() as tx:
        row = tx.read("p2_vector_projections", target.vector_id)
    assert row["operation_id"] == "op1"


async def test_changed_payload_and_deleted_projection_cannot_be_reinserted(adapter):
    vectors, _, _, ctx, target = adapter
    first = ProjectionRequest(
        operation_id="op1", target=target, vector=(1, 0), deadline_at=ctx.deadline_at
    )
    await vectors.project(ctx, first)
    with pytest.raises(FoundationError) as error:
        await vectors.project(ctx, first.model_copy(update={"vector": (0, 1)}))
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    removed = await vectors.delete(ctx, target, "delete1")
    assert removed.state == "absent"
    with pytest.raises(FoundationError) as error:
        await vectors.project(ctx, first)
    assert error.value.code == ErrorCode.MEMORY_GONE


async def test_unsearchable_projection_does_not_prove_absence(adapter):
    vectors, wire, _, ctx, target = adapter
    await vectors.project(
        ctx,
        ProjectionRequest(
            operation_id="op1", target=target, vector=(1, 0), deadline_at=ctx.deadline_at
        ),
    )
    # A frozen/archived segment or a bounded search can hide an existing vector.
    wire.deleted.add(target.vector_id)
    assert (await vectors.inspect(ctx, target, "op1")).state == "unknown"


async def test_lost_delete_ack_cannot_be_resolved_by_search_miss(adapter):
    vectors, wire, _, ctx, target = adapter
    await vectors.project(
        ctx,
        ProjectionRequest(
            operation_id="op1", target=target, vector=(1, 0), deadline_at=ctx.deadline_at
        ),
    )
    wire.fail_delete = True
    assert (await vectors.delete(ctx, target, "delete1")).state == "unknown"
    assert (await vectors.inspect(ctx, target, "delete1")).state == "unknown"
    # Retrying the original tombstone command is safe in current P2.
    wire.fail_delete = False
    assert (await vectors.delete(ctx, target, "delete1")).state == "absent"


async def test_delete_before_projection_prevents_late_insert(adapter):
    vectors, _, _, ctx, target = adapter
    assert (await vectors.delete(ctx, target, "delete1")).state == "absent"
    with pytest.raises(FoundationError) as error:
        await vectors.project(
            ctx,
            ProjectionRequest(
                operation_id="op1", target=target, vector=(1, 0), deadline_at=ctx.deadline_at
            ),
        )
    assert error.value.code == ErrorCode.MEMORY_GONE


async def test_duplicate_search_rows_are_not_exact_projection_evidence(adapter, monkeypatch):
    vectors, wire, _, ctx, target = adapter
    await vectors.project(
        ctx,
        ProjectionRequest(
            operation_id="op1", target=target, vector=(1, 0), deadline_at=ctx.deadline_at
        ),
    )
    original = wire.search_vectors

    async def duplicated(*args, **kwargs):
        hits = await original(*args, **kwargs)
        return hits + hits

    monkeypatch.setattr(wire, "search_vectors", duplicated)
    with pytest.raises(FoundationError) as error:
        await vectors.inspect(ctx, target, "op1")
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


@pytest.mark.parametrize("metric", ["cosine", "inner_product"])
async def test_generation_search_accepts_equivalent_unit_vector_metrics(adapter, metric):
    from aether_agent_memory.recall.contracts.foundation import ChunkSearchRequest, EmbeddingSpace

    vectors, _, _, ctx, target = adapter
    await vectors.project(
        ctx,
        ProjectionRequest(
            operation_id="op1", target=target, vector=(1, 0), deadline_at=ctx.deadline_at
        ),
    )
    request = ChunkSearchRequest(
        operation_id="query1",
        selection={},
        vector=(1, 0),
        limit=2,
        deadline_at=ctx.deadline_at,
        model_space=EmbeddingSpace(
            model_space="model-v1",
            model_id="fixture",
            model_revision="v1",
            dimensions=2,
            tokenizer_id="fixture",
            query_prefix="",
            passage_prefix="",
            normalization="unit",
            metric=metric,
            max_input_tokens=512,
        ),
    )
    result = await vectors.generation_search().search(ctx, request)
    assert [hit.vector_id for hit in result.hits] == [target.vector_id]


@pytest.mark.parametrize("method", ["project", "search"])
async def test_expired_short_request_deadline_stops_before_p2_or_intent(
    adapter, monkeypatch, method
):
    vectors, wire, foundation, ctx, target = adapter
    calls = []

    async def unexpected(*args, **kwargs):
        calls.append(method)
        return []

    monkeypatch.setattr(wire, "upsert_vectors", unexpected)
    monkeypatch.setattr(wire, "search_vectors", unexpected)
    deadline = later(foundation.identity.clock(), -1)
    request = (
        ProjectionRequest(
            operation_id="expired", target=target, vector=(1, 0), deadline_at=deadline
        )
        if method == "project"
        else VectorSearchRequest(
            selection={}, vector=(1, 0), model_space="model-v1", limit=2, deadline_at=deadline
        )
    )
    with pytest.raises(FoundationError) as error:
        await getattr(vectors, method)(ctx, request)
    assert error.value.code == ErrorCode.DEADLINE_EXCEEDED
    assert calls == []
    with foundation.uow.transaction() as tx:
        assert tx.read("p2_vector_projections", target.vector_id) is None


async def test_projection_deadline_preserves_original_intent_and_never_reinserts(
    adapter, monkeypatch
):
    vectors, wire, foundation, ctx, target = adapter
    original = wire.upsert_vectors
    calls = []
    cancelled = asyncio.Event()

    async def committed_without_reply(records, **kwargs):
        calls.append(records[0].request_id)
        await original(records, **kwargs)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(wire, "upsert_vectors", committed_without_reply)
    request = ProjectionRequest(
        operation_id="timed-out-write",
        target=target,
        vector=(1, 0),
        deadline_at=later(foundation.identity.clock(), 0.1),
    )
    with pytest.raises(FoundationError) as error:
        await asyncio.wait_for(vectors.project(ctx, request), timeout=1)
    assert error.value.code == ErrorCode.DEADLINE_EXCEEDED
    assert cancelled.is_set()
    with foundation.uow.transaction() as tx:
        row = tx.read("p2_vector_projections", target.vector_id)
    assert row["operation_id"] == "timed-out-write" and not row["deleted"]
    checked = await vectors.project(
        ctx, request.model_copy(update={"deadline_at": ctx.deadline_at})
    )
    assert checked.state == "verified"
    assert calls == ["timed-out-write"]


async def test_search_wait_is_bounded_by_short_request_deadline(adapter, monkeypatch):
    vectors, wire, foundation, ctx, _ = adapter

    async def no_reply(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(wire, "search_vectors", no_reply)
    request = VectorSearchRequest(
        selection={},
        vector=(1, 0),
        model_space="model-v1",
        limit=2,
        deadline_at=later(foundation.identity.clock(), 0.1),
    )
    with pytest.raises(FoundationError) as error:
        await asyncio.wait_for(vectors.search(ctx, request), timeout=1)
    assert error.value.code == ErrorCode.DEADLINE_EXCEEDED


async def test_inspect_rechecks_memory_grant_after_remote_reply(adapter):
    vectors, wire, foundation, ctx, target = adapter
    await vectors.project(
        ctx,
        ProjectionRequest(
            operation_id="op1",
            target=target,
            vector=(1, 0),
            deadline_at=ctx.deadline_at,
        ),
    )
    bob = ctx.principal.model_copy(
        update={
            "principal_id": "bob",
            "home_scope": ctx.principal.home_scope.model_copy(update={"user_id": "bob"}),
        }
    )
    principals = [(sha256(b"alice").hexdigest(), ctx.principal), (sha256(b"bob").hexdigest(), bob)]
    foundation.identity.provision(
        principals,
        [
            AuthorizationGrant(
                grant_id="shared",
                grantee_id="bob",
                grantee_tenant_id="t1",
                resource=vectors.ref(target),
                permissions=(Permission.READ,),
                revision=1,
            )
        ],
    )
    reader = foundation.identity.context("bob", timeout_seconds=30)
    wire.search_drops_after_reply = lambda: foundation.identity.provision(principals)
    with pytest.raises(FoundationError) as error:
        await vectors.inspect(reader, target, "read1")
    assert error.value.code == ErrorCode.FORBIDDEN


@pytest.mark.parametrize("copies", [2, 3])
async def test_search_rejects_duplicate_ids_and_excess_rows(adapter, monkeypatch, copies):
    vectors, wire, _, ctx, target = adapter
    await vectors.project(
        ctx,
        ProjectionRequest(
            operation_id="op1",
            target=target,
            vector=(1, 0),
            deadline_at=ctx.deadline_at,
        ),
    )
    original = wire.search_vectors

    async def duplicated(*args, **kwargs):
        return (await original(*args, **kwargs)) * copies

    monkeypatch.setattr(wire, "search_vectors", duplicated)
    with pytest.raises(FoundationError) as error:
        await vectors.search(
            ctx,
            VectorSearchRequest(
                selection={},
                vector=(1, 0),
                model_space="model-v1",
                limit=2,
                deadline_at=ctx.deadline_at,
            ),
        )
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


async def test_concurrent_duplicate_and_delete_fence_late_projection(adapter, monkeypatch):
    vectors, wire, _, ctx, target = adapter
    original = wire.upsert_vectors
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def delayed(records, **kwargs):
        calls.append(records[0].request_id)
        entered.set()
        await release.wait()
        await original(records, **kwargs)

    monkeypatch.setattr(wire, "upsert_vectors", delayed)
    request = ProjectionRequest(
        operation_id="concurrent",
        target=target,
        vector=(1, 0),
        deadline_at=ctx.deadline_at,
    )
    pending = asyncio.create_task(vectors.project(ctx, request))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert (await vectors.project(ctx, request)).state == "unknown"
        assert (await vectors.delete(ctx, target, "delete1")).state == "absent"
    finally:
        release.set()
        result = await asyncio.wait_for(pending, timeout=1)
    assert result.state == "absent"
    assert calls == ["concurrent"]
    assert await wire.search_vectors((1, 0)) == []
