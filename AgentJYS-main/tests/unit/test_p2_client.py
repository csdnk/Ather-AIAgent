# ruff: noqa: N802, N815 - fake methods mirror generated gRPC names

from __future__ import annotations

import json

import pytest

from aether_agent_memory.p2.client import P2GrpcClient
from aether_agent_memory.p2.models import EmbeddingRecord


class _Message:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)


class _Proto:
    GetObjectRangeRequest = _Message
    CreateCollectionRequest = _Message
    InsertVectorRequest = _Message
    DeleteVectorsRequest = _Message
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


async def test_half_open_object_range_adapts_wire_end_and_checks_length():
    from types import SimpleNamespace

    from aether_agent_memory.p2.client import P2UnavailableError

    class ObjectStub:
        async def GetObjectRange(self, request, *, timeout):
            assert request.start == 2 and request.end == 4
            assert timeout == 10
            return _Message(data=self.data)

    stub = ObjectStub()
    stub.data = b"cde"
    client = P2GrpcClient("fixture")
    client._channel = object()
    client._pb = _Proto
    client._grpc = SimpleNamespace(ObjectServiceStub=lambda channel: stub)
    assert await client.read_range("key", 2, 5) == b"cde"
    assert await client.read_range("key", 2, 2) == b""
    stub.data = b"cd"
    with pytest.raises(P2UnavailableError):
        await client.read_range("key", 2, 5)


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


# Sync channels are owned independently of asyncio channels and enforce deadlines.
def test_sync_object_client_reads_and_writes_without_asyncio_channel():
    from types import SimpleNamespace

    class SyncObjectStub:
        def __init__(self):
            self.data = None
            self.calls = []

        def CreateBucket(self, request, *, timeout):
            self.calls.append(("bucket", request.bucket, timeout))

        def PutObject(self, request, *, timeout):
            self.calls.append(("put", request.key, timeout))
            self.data = request.data
            return _Message(
                bucket=request.bucket,
                key=request.key,
                etag="etag",
                size=len(request.data),
                md5_hex=None,
                blake3_hex=None,
            )

        def GetObject(self, request, *, timeout):
            self.calls.append(("get", request.key, timeout))
            return _Message(data=self.data)

    stub = SyncObjectStub()
    client = P2GrpcClient("fixture", timeout_seconds=2)
    client._sync_channel = object()
    client._sync_pb = SimpleNamespace(
        CreateBucketRequest=_Message, PutObjectRequest=_Message, GetObjectRequest=_Message
    )
    client._sync_grpc = SimpleNamespace(ObjectServiceStub=lambda channel: stub)
    meta = client.put_object_sync("input", b"immutable bytes")
    assert meta.size == 15
    assert client.get_object_sync("input") == b"immutable bytes"
    assert all(call[2] == 2 for call in stub.calls)
    assert client._channel is None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "outcome", ["confirmed", "delayed", "different_bytes", "different_key", "missing", "denied"]
)
async def test_uncertain_object_write_reads_original_result_without_repeating_write(
    asynchronous, outcome
):
    from types import SimpleNamespace

    import grpc

    from aether_agent_memory.p2.client import P2UnavailableError

    class WriteFailure(grpc.RpcError):
        def code(self):
            return (
                grpc.StatusCode.PERMISSION_DENIED
                if outcome == "denied"
                else grpc.StatusCode.INTERNAL
            )

    class NotVisibleYet(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.NOT_FOUND

    failure = WriteFailure()
    calls = []
    metadata = _Message(bucket="p3-memory", key="original", etag="actual-etag", size=4)
    if outcome == "different_key":
        metadata.key = "foreign"

    def create(request, *, timeout):
        calls.append("create")

    def write(request, *, timeout):
        calls.append("put")
        assert request.key == "original" and request.data == b"body"
        raise failure

    def read(request, *, timeout):
        calls.append("get")
        assert request.key == "original" and request.bucket == "p3-memory"
        if outcome == "missing":
            raise failure
        if outcome == "delayed" and calls.count("get") == 1:
            raise NotVisibleYet()
        return _Message(
            data=b"different" if outcome == "different_bytes" else b"body", meta=metadata
        )

    async def create_async(*args, **kwargs):
        return create(*args, **kwargs)

    async def write_async(*args, **kwargs):
        return write(*args, **kwargs)

    async def read_async(*args, **kwargs):
        return read(*args, **kwargs)

    stub = SimpleNamespace(
        CreateBucket=create_async if asynchronous else create,
        PutObject=write_async if asynchronous else write,
        GetObject=read_async if asynchronous else read,
    )
    client = P2GrpcClient("component", timeout_seconds=0.05)
    proto = SimpleNamespace(
        CreateBucketRequest=_Message, PutObjectRequest=_Message, GetObjectRequest=_Message
    )
    if asynchronous:
        client._channel = object()
        client._pb, client._grpc = proto, SimpleNamespace(ObjectServiceStub=lambda channel: stub)
    else:
        client._sync_channel = object()
        client._sync_pb = proto
        client._sync_grpc = SimpleNamespace(ObjectServiceStub=lambda channel: stub)

    async def invoke():
        if asynchronous:
            return await client.put_object("original", b"body")
        return client.put_object_sync("original", b"body")

    if outcome in {"confirmed", "delayed"}:
        result = await invoke()
        assert result.etag == "actual-etag" and result.key == "original" and result.size == 4
    else:
        with pytest.raises((grpc.RpcError, P2UnavailableError)):
            await invoke()
    assert calls[:2] == ["create", "put"]
    if outcome == "missing":
        assert calls[2:] and set(calls[2:]) == {"get"}
    else:
        expected_reads = [] if outcome == "denied" else ["get"] * (2 if outcome == "delayed" else 1)
        assert calls[2:] == expected_reads


@pytest.mark.parametrize("confirmed", [0, 2])
async def test_delete_rejects_incomplete_or_unbound_acknowledgement(confirmed):
    from types import SimpleNamespace

    from aether_agent_memory.p2.client import P2UnavailableError

    class DeleteStub:
        async def DeleteVectors(self, request, *, timeout):
            assert request.ids == ["vector1"]
            return _Message(deleted=confirmed)

    client = P2GrpcClient("fixture")
    client._channel = object()
    client._pb = _Proto
    client._grpc = SimpleNamespace(VectorServiceStub=lambda channel: DeleteStub())
    with pytest.raises(P2UnavailableError):
        await client.delete_vectors(["vector1"])
