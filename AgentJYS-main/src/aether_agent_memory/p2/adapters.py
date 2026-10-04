"""Adapters that attach P3 extension points to the P2 gRPC client."""

from aether_agent_memory.core.enums import StorageTier
from aether_agent_memory.p2.client import P2GrpcClient
from aether_agent_memory.p2.models import EmbeddingRecord
from aether_agent_memory.placement import P2Ref


class P2VectorSink:
    def __init__(self, client: P2GrpcClient) -> None:
        self._client = client

    async def upsert(self, records: list[EmbeddingRecord]) -> None:
        await self._client.upsert_vectors(records)


class P2StorageClient:
    """P3 StorageClient implementation backed by P2 ObjectService."""

    def __init__(self, client: P2GrpcClient, *, tier: StorageTier = StorageTier.L3_OBJECT) -> None:
        self._client = client
        self._tier = tier

    async def put(self, key: str, data: bytes) -> P2Ref:
        meta = await self._client.put_object(key, data)
        return P2Ref(
            segment_id=f"object/{meta.bucket}",
            object_key=meta.key,
            tier=self._tier,
        )

    async def get(self, key: str) -> bytes | None:
        data = await self._client.get_object(key)
        return bytes(data) if data is not None else None

    async def delete(self, key: str) -> bool:
        return bool(await self._client.delete_object(key))
