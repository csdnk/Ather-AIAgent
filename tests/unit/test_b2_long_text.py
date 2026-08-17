import pytest

from aether_agent_memory.b2.long_text import chunk_text


@pytest.mark.unit
def test_chunk_text_preserves_content_with_overlap() -> None:
    chunks = chunk_text("abcdefghijklmnopqrstuvwxyz", chunk_size=10, overlap=2)
    assert chunks == ["abcdefghij", "ijklmnopqr", "qrstuvwxyz", "yz"]


@pytest.mark.unit
def test_chunk_text_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_text("text", chunk_size=10, overlap=10)
