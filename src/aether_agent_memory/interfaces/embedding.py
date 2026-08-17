from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]: ...
