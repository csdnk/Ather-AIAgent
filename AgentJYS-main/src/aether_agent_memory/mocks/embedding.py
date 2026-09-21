import hashlib


class MockEmbeddingClient:
    def __init__(self, dim: int = 32) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        raw = hashlib.shake_256(text.encode("utf-8")).digest(self._dim)
        return [(b / 127.5) - 1.0 for b in raw]
