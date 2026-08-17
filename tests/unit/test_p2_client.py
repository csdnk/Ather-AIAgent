# ruff: noqa: N802, N815 - fake methods mirror generated gRPC names

from __future__ import annotations

import json

import pytest

from aether_agent_memory.b1.models import EmbeddingRecord
from aether_agent_memory.p2.client import P2GrpcClient


class _Message:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)


class _Proto:
    CreateCollectionRequest = _Message
    InsertVectorRequest = _Message
    VectorRecord = _Message
    SearchVectorRequest = _Message
    ListSegmentsRequest = _Message
    SegmentRef = _Message
    OnMigrateCompleteRequest = _Message
    OnMigrateFailedRequest = _Message


class _VectorStub:
    last_insert: _Message | None = None
    last_timeout: float | None = None

    def __init__(self, _channel: object) -> None:
        return None

    async def CreateCollection(self, _request: _Message, *, timeout: float) -> None:
        type(self).last_timeout = timeout

    async def InsertVector(self, request: _Message, *, timeout: float) -> None:
        type(self).last_insert = request
        type(self).last_timeout = timeout

    async def SearchVector(self, _request: _Message, *, timeout: float) -> _Message:
        type(self).last_timeout = timeout
        return _Message(
            hits=[
                _Message(
                    id="memory-1:source-1:0000",
                    score=0.91,
                    metadata_json='{"tenant_id":"tenant-1","chunk_text":"hello"}',
                )
            ]
        )


class _Grpc:
    VectorServiceStub = _VectorStub


class _SegmentStub:
    calls: list[tuple[str, _Message, float]] = []

    def __init__(self, _channel: object) -> None:
        return None

    async def ListSegments(self, request: _Message, *, timeout: float) -> _Message:
        type(self).calls.append(("list", request, timeout))
        return _Message(
            segments=[
                _Message(
                    engine="object/default",
                    segment_id="p3-memory",
                    state="sealed",
                    route_epoch=4,
                    block_ids=["old-block"],
                )
            ]
        )

    async def Freeze(self, request: _Message, *, timeout: float) -> _Message:
        type(self).calls.append(("freeze", request, timeout))
        return _Message(
            ok=True,
            engine=request.engine,
            segment_id=request.segment_id,
            migration_id=request.migration_id,
            route_epoch=request.expected_route_epoch,
            idempotent=False,
            state="frozen",
            block_ids=["old-block"],
        )

    async def Unfreeze(self, request: _Message, *, timeout: float) -> _Message:
        type(self).calls.append(("unfreeze", request, timeout))
        return _Message(ok=True, state="sealed")

    async def OnMigrateComplete(self, request: _Message, *, timeout: float) -> _Message:
        type(self).calls.append(("complete", request, timeout))
        return _Message(
            ok=True,
            engine=request.engine,
            segment_id=request.segment_id,
            migration_id=request.migration_id,
            route_epoch=request.expected_route_epoch + 1,
            idempotent=False,
            state="sealed",
            block_ids=request.new_block_ids,
            outcome="completed",
        )

    async def OnMigrateFailed(self, request: _Message, *, timeout: float) -> _Message:
        type(self).calls.append(("failed", request, timeout))
        return _Message(ok=True, state="sealed", outcome="failed")


class _ControlGrpc:
    SegmentControlServiceStub = _SegmentStub


@pytest.mark.unit
async def test_p2_client_preserves_vector_metadata_and_search_hits() -> None:
    client = P2GrpcClient("p2:50052", timeout_seconds=3.5)
    client._channel = object()
    client._pb = _Proto
    client._grpc = _Grpc
    record = EmbeddingRecord(
        request_id="request-1",
        trace_id="trace-1",
        source_id="source-1",
        object_id="object-1",
        chunk_id="memory-1:source-1:0000",
        chunk_text="hello",
        vector=[0.6, 0.8],
        embedding_model="b1-test",
        metadata={"tenant_id": "tenant-1"},
    )

    await client.upsert_vectors([record])
    assert _VectorStub.last_insert is not None
    inserted_records = _VectorStub.last_insert.records
    assert json.loads(inserted_records[0].metadata_json)["chunk_text"] == "hello"

    hits = await client.search_vectors([0.6, 0.8], top_k=3)

    assert hits[0].id == record.chunk_id
    assert hits[0].metadata["chunk_text"] == "hello"
    assert _VectorStub.last_timeout == 3.5


@pytest.mark.unit
async def test_p2_client_exposes_segment_control_with_route_epoch() -> None:
    _SegmentStub.calls.clear()
    client = P2GrpcClient("p2:50052", timeout_seconds=2.25)
    client._channel = object()
    client._pb = _Proto
    client._grpc = _ControlGrpc

    segments = await client.list_segments(engine="object/default")
    frozen = await client.freeze_segment(
        "p3-memory",
        engine="object/default",
        migration_id="migration-1",
        expected_route_epoch=segments[0].route_epoch,
    )
    completed = await client.complete_migration(
        "p3-memory",
        engine="object/default",
        migration_id="migration-1",
        new_block_ids=["new-block"],
        expected_route_epoch=frozen.route_epoch,
    )
    failed = await client.fail_migration(
        "p3-memory",
        engine="object/default",
        migration_id="migration-2",
        reason="test rollback",
        expected_route_epoch=completed.route_epoch,
    )

    assert segments[0].route_epoch == 4
    assert frozen.state == "frozen"
    assert completed.block_ids == ["new-block"]
    assert failed.outcome == "failed"
    assert [call[0] for call in _SegmentStub.calls] == ["list", "freeze", "complete", "failed"]
    assert _SegmentStub.calls[1][1].expected_route_epoch == 4
    assert _SegmentStub.calls[2][1].expected_route_epoch == 4
