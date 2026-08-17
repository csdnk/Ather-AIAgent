"""Async P2 gRPC client generated from the shared engine contract.

The generated protobuf module is intentionally not committed. Run
``python scripts/generate_proto.py`` after installing the Python dependencies.
This keeps Python and Rust on the single ``engine/proto/aether_engine.proto``
source of truth.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from aether_agent_memory.b1.models import EmbeddingRecord


class P2UnavailableError(RuntimeError):
    """Raised when generated P2 stubs are unavailable or P2 cannot be reached."""


@dataclass(frozen=True)
class P2ObjectMeta:
    bucket: str
    key: str
    etag: str
    size: int


@dataclass(frozen=True)
class P2VectorHit:
    id: str
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class P2SegmentInfo:
    engine: str
    segment_id: str
    state: str
    route_epoch: int
    block_ids: list[str]
    active_migration_id: str | None = None
    last_migration_id: str | None = None
    last_outcome: str | None = None


@dataclass(frozen=True)
class P2MigrationAck:
    ok: bool
    engine: str
    segment_id: str
    migration_id: str
    route_epoch: int
    idempotent: bool
    state: str
    block_ids: list[str]
    outcome: str | None = None


class P2GrpcClient:
    """Thin async client for the P2 Object, Vector, and Segment APIs."""

    def __init__(
        self,
        endpoint: str,
        *,
        bucket: str = "p3-memory",
        collection: str = "p3",
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("P2 gRPC timeout must be positive")
        self.endpoint = endpoint
        self.bucket = bucket
        self.collection = collection
        self.timeout_seconds = timeout_seconds
        self._channel: Any | None = None
        self._pb: Any | None = None
        self._grpc: Any | None = None

    async def connect(self) -> None:
        if self._channel is not None:
            return
        try:
            import grpc

            from aether_agent_memory.p2.generated import (  # type: ignore[import-untyped]
                aether_engine_pb2 as pb,
            )
            from aether_agent_memory.p2.generated import (
                aether_engine_pb2_grpc as pb_grpc,
            )
        except ImportError as exc:
            raise P2UnavailableError(
                "P2 protobuf stubs are missing. Run: python scripts/generate_proto.py"
            ) from exc
        self._channel = grpc.aio.insecure_channel(self.endpoint)
        self._pb = pb
        self._grpc = pb_grpc

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None

    async def ensure_collection(self, dimension: int, *, collection: str | None = None) -> None:
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        stub = self._grpc.VectorServiceStub(self._channel)
        await stub.CreateCollection(
            self._pb.CreateCollectionRequest(
                collection=collection or self.collection, dimension=dimension
            ),
            timeout=self.timeout_seconds,
        )

    async def upsert_vectors(
        self, records: Sequence[EmbeddingRecord], *, collection: str | None = None
    ) -> None:
        if not records:
            return
        target_collection = collection or self.collection
        await self.ensure_collection(len(records[0].vector), collection=target_collection)
        assert self._pb is not None and self._grpc is not None
        stub = self._grpc.VectorServiceStub(self._channel)
        payload = [
            self._pb.VectorRecord(
                id=record.chunk_id,
                values=record.vector,
                metadata_json=json.dumps(
                    {
                        **record.metadata,
                        "request_id": record.request_id,
                        "source_id": record.source_id,
                        "object_id": record.object_id,
                        "trace_id": record.trace_id,
                        "embedding_model": record.embedding_model,
                        "chunk_text": record.chunk_text,
                    },
                    ensure_ascii=False,
                ),
            )
            for record in records
        ]
        await stub.InsertVector(
            self._pb.InsertVectorRequest(collection=target_collection, records=payload),
            timeout=self.timeout_seconds,
        )

    async def search_vectors(
        self,
        query: Sequence[float],
        *,
        top_k: int = 5,
        collection: str | None = None,
    ) -> list[P2VectorHit]:
        if not query:
            raise ValueError("P2 vector query must not be empty")
        if top_k <= 0:
            raise ValueError("P2 vector top_k must be positive")
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        try:
            response = await self._grpc.VectorServiceStub(self._channel).SearchVector(
                self._pb.SearchVectorRequest(
                    collection=collection or self.collection,
                    query=[float(value) for value in query],
                    top_k=top_k,
                ),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            if self._is_not_found(exc):
                return []
            raise
        hits: list[P2VectorHit] = []
        for hit in response.hits:
            metadata_raw = getattr(hit, "metadata_json", None)
            try:
                metadata = json.loads(metadata_raw) if metadata_raw else {}
            except (TypeError, json.JSONDecodeError) as exc:
                raise P2UnavailableError("P2 returned invalid vector metadata") from exc
            if not isinstance(metadata, dict):
                raise P2UnavailableError("P2 returned non-object vector metadata")
            hits.append(P2VectorHit(id=hit.id, score=float(hit.score), metadata=metadata))
        return hits

    async def list_segments(self, *, engine: str = "") -> list[P2SegmentInfo]:
        """Return P2 segment state used to establish a migration epoch."""
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        response = await self._grpc.SegmentControlServiceStub(self._channel).ListSegments(
            self._pb.ListSegmentsRequest(engine=engine),
            timeout=self.timeout_seconds,
        )
        segments = getattr(response, "segments", None)
        if segments:
            return [self._segment_info(item) for item in segments]
        return [
            P2SegmentInfo(
                engine=engine,
                segment_id=str(segment_id),
                state="",
                route_epoch=0,
                block_ids=[],
            )
            for segment_id in getattr(response, "segment_ids", [])
        ]

    async def freeze_segment(
        self,
        segment_id: str,
        *,
        engine: str,
        migration_id: str,
        expected_route_epoch: int | None = None,
    ) -> P2MigrationAck:
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        request = self._pb.SegmentRef(
            segment_id=segment_id,
            engine=engine,
            migration_id=migration_id,
        )
        if expected_route_epoch is not None:
            request.expected_route_epoch = expected_route_epoch
        response = await self._grpc.SegmentControlServiceStub(self._channel).Freeze(
            request,
            timeout=self.timeout_seconds,
        )
        return self._migration_ack(response)

    async def unfreeze_segment(
        self,
        segment_id: str,
        *,
        engine: str,
        migration_id: str,
        expected_route_epoch: int | None = None,
    ) -> P2MigrationAck:
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        request = self._pb.SegmentRef(
            segment_id=segment_id,
            engine=engine,
            migration_id=migration_id,
        )
        if expected_route_epoch is not None:
            request.expected_route_epoch = expected_route_epoch
        response = await self._grpc.SegmentControlServiceStub(self._channel).Unfreeze(
            request,
            timeout=self.timeout_seconds,
        )
        return self._migration_ack(response)

    async def complete_migration(
        self,
        segment_id: str,
        *,
        engine: str,
        migration_id: str,
        new_block_ids: Sequence[str],
        expected_route_epoch: int | None = None,
    ) -> P2MigrationAck:
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        request = self._pb.OnMigrateCompleteRequest(
            segment_id=segment_id,
            new_block_ids=list(new_block_ids),
            engine=engine,
            migration_id=migration_id,
        )
        if expected_route_epoch is not None:
            request.expected_route_epoch = expected_route_epoch
        response = await self._grpc.SegmentControlServiceStub(self._channel).OnMigrateComplete(
            request,
            timeout=self.timeout_seconds,
        )
        return self._migration_ack(response)

    async def fail_migration(
        self,
        segment_id: str,
        *,
        engine: str,
        migration_id: str,
        reason: str,
        expected_route_epoch: int | None = None,
    ) -> P2MigrationAck:
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        request = self._pb.OnMigrateFailedRequest(
            segment_id=segment_id,
            reason=reason,
            engine=engine,
            migration_id=migration_id,
        )
        if expected_route_epoch is not None:
            request.expected_route_epoch = expected_route_epoch
        response = await self._grpc.SegmentControlServiceStub(self._channel).OnMigrateFailed(
            request,
            timeout=self.timeout_seconds,
        )
        return self._migration_ack(response)

    async def put_object(self, key: str, data: bytes) -> P2ObjectMeta:
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        stub = self._grpc.ObjectServiceStub(self._channel)
        await stub.CreateBucket(
            self._pb.CreateBucketRequest(bucket=self.bucket), timeout=self.timeout_seconds
        )
        meta = await stub.PutObject(
            self._pb.PutObjectRequest(bucket=self.bucket, key=key, data=data),
            timeout=self.timeout_seconds,
        )
        return P2ObjectMeta(bucket=meta.bucket, key=meta.key, etag=meta.etag, size=meta.size)

    async def get_object(self, key: str) -> bytes | None:
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        try:
            response = await self._grpc.ObjectServiceStub(self._channel).GetObject(
                self._pb.GetObjectRequest(bucket=self.bucket, key=key),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            if self._is_not_found(exc):
                return None
            raise
        return bytes(response.data)

    async def delete_object(self, key: str) -> bool:
        await self.connect()
        assert self._pb is not None and self._grpc is not None
        try:
            await self._grpc.ObjectServiceStub(self._channel).DeleteObject(
                self._pb.GetObjectRequest(bucket=self.bucket, key=key),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            raise
        return True

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        code = getattr(exc, "code", lambda: None)()
        return code is not None and str(code).endswith("NOT_FOUND")

    @staticmethod
    def _segment_info(value: Any) -> P2SegmentInfo:
        return P2SegmentInfo(
            engine=str(getattr(value, "engine", "")),
            segment_id=str(value.segment_id),
            state=str(getattr(value, "state", "")),
            route_epoch=int(getattr(value, "route_epoch", 0)),
            block_ids=[str(block_id) for block_id in getattr(value, "block_ids", [])],
            active_migration_id=getattr(value, "active_migration_id", None) or None,
            last_migration_id=getattr(value, "last_migration_id", None) or None,
            last_outcome=getattr(value, "last_outcome", None) or None,
        )

    @staticmethod
    def _migration_ack(value: Any) -> P2MigrationAck:
        return P2MigrationAck(
            ok=bool(getattr(value, "ok", True)),
            engine=str(getattr(value, "engine", "")),
            segment_id=str(getattr(value, "segment_id", "")),
            migration_id=str(getattr(value, "migration_id", "")),
            route_epoch=int(getattr(value, "route_epoch", 0)),
            idempotent=bool(getattr(value, "idempotent", False)),
            state=str(getattr(value, "state", "")),
            block_ids=[str(block_id) for block_id in getattr(value, "block_ids", [])],
            outcome=getattr(value, "outcome", None) or None,
        )
