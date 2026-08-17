import pytest

from aether_agent_memory.mocks.embedding import MockEmbeddingClient


@pytest.mark.unit
async def test_embed_one_returns_fixed_dim() -> None:
    client = MockEmbeddingClient(dim=32)
    vec = await client.embed_one("hello world")
    assert len(vec) == 32
    assert all(-1.0 <= v <= 1.0 for v in vec)


@pytest.mark.unit
async def test_embed_one_deterministic() -> None:
    client = MockEmbeddingClient(dim=32)
    v1 = await client.embed_one("hello")
    v2 = await client.embed_one("hello")
    assert v1 == v2


@pytest.mark.unit
async def test_embed_one_different_text_different_vec() -> None:
    client = MockEmbeddingClient(dim=32)
    v1 = await client.embed_one("hello")
    v2 = await client.embed_one("world")
    assert v1 != v2


@pytest.mark.unit
async def test_embed_batch() -> None:
    client = MockEmbeddingClient(dim=16)
    vecs = await client.embed(["a", "b", "c"])
    assert len(vecs) == 3
    assert all(len(v) == 16 for v in vecs)
    assert vecs[0] == await client.embed_one("a")


@pytest.mark.unit
async def test_embed_empty_batch() -> None:
    client = MockEmbeddingClient(dim=8)
    assert await client.embed([]) == []
