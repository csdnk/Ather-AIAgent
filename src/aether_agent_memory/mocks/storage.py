from aether_agent_memory.core.enums import StorageTier
from aether_agent_memory.core.memory import P2Ref


class MockStorageClient:
    def __init__(self, default_tier: StorageTier = StorageTier.L0_DRAM) -> None:
        self._store: dict[str, bytes] = {}
        self._default_tier = default_tier

    async def put(self, key: str, data: bytes) -> P2Ref:
        self._store[key] = data
        return P2Ref(segment_id="mock", object_key=key, tier=self._default_tier)

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None
