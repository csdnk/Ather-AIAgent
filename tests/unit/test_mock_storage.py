import pytest

from aether_agent_memory.core.enums import StorageTier
from aether_agent_memory.mocks.storage import MockStorageClient


@pytest.mark.unit
async def test_put_and_get() -> None:
    client = MockStorageClient()
    ref = await client.put("key-1", b"payload")
    assert ref.object_key == "key-1"
    assert ref.segment_id == "mock"
    assert await client.get("key-1") == b"payload"


@pytest.mark.unit
async def test_put_tier() -> None:
    client = MockStorageClient(default_tier=StorageTier.L1_NVME)
    ref = await client.put("key-2", b"data")
    assert ref.tier == StorageTier.L1_NVME


@pytest.mark.unit
async def test_get_missing_returns_none() -> None:
    client = MockStorageClient()
    assert await client.get("nope") is None


@pytest.mark.unit
async def test_delete_existing() -> None:
    client = MockStorageClient()
    await client.put("key-3", b"x")
    assert await client.delete("key-3") is True
    assert await client.get("key-3") is None


@pytest.mark.unit
async def test_delete_missing_returns_false() -> None:
    client = MockStorageClient()
    assert await client.delete("ghost") is False
