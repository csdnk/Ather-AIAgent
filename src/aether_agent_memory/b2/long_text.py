"""Development-only helpers for inspecting B2 long-text input."""

from __future__ import annotations


def chunk_text(text: str, *, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Split text for local diagnostics; production chunking belongs to B1."""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller than chunk_size")
    normalized = " ".join(text.split())
    if not normalized:
        return []
    step = chunk_size - overlap
    return [normalized[start : start + chunk_size] for start in range(0, len(normalized), step)]
