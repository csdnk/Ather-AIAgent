import pytest

from aether_agent_memory.core.enums import StorageTier
from aether_agent_memory.p2.adapters import P2StorageClient, P2VectorSink
from aether_agent_memory.p2.client import P2ObjectMeta
from aether_agent_memory.p2.models import EmbeddingRecord


class FakeP2Client:
    def __init__(self) -> None:
        self.records: list[EmbeddingRecord] = []
        self.objects: dict[str, bytes] = {}

    async def upsert_vectors(self, records: list[EmbeddingRecord]) -> None:
        self.records.extend(records)

    async def put_object(self, key: str, data: bytes) -> P2ObjectMeta:
        self.objects[key] = data
        return P2ObjectMeta(bucket="p3", key=key, etag="etag", size=len(data))

    async def get_object(self, key: str) -> bytes | None:
        return self.objects.get(key)

    async def delete_object(self, key: str) -> bool:
        return self.objects.pop(key, None) is not None


@pytest.mark.unit
async def test_p2_adapters_preserve_vector_and_object_traceability() -> None:
    client = FakeP2Client()
    record = EmbeddingRecord(
        request_id="request-1",
        trace_id="trace-1",
        source_id="source-1",
        object_id="object-1",
        chunk_id="chunk-1",
        chunk_text="semantic payload",
        vector=[0.1, 0.2],
        embedding_model="mock",
    )

    await P2VectorSink(client).upsert([record])  # type: ignore[arg-type]
    storage = P2StorageClient(client, tier=StorageTier.L3_OBJECT)  # type: ignore[arg-type]
    reference = await storage.put("object-1", b"payload")

    assert client.records == [record]
    assert reference.segment_id == "object/p3"
    assert reference.tier == StorageTier.L3_OBJECT
    assert await storage.get("object-1") == b"payload"
    assert await storage.delete("object-1") is True
    assert await storage.delete("object-1") is False
