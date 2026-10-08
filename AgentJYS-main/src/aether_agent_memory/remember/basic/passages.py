"""Resolve qualified vector chunks against the exact original Memory body."""

from hashlib import sha256

from aether_agent_memory.remember.contracts.foundation import ChunkDescriptor, OriginalPassage


def original_passages(
    content: str, body_hash: str, chunks: tuple[ChunkDescriptor, ...]
) -> tuple[OriginalPassage, ...]:
    if sha256(content.encode("utf-8")).hexdigest() != body_hash:
        raise ValueError("original body changed")
    result = []
    seen = set()
    for chunk in chunks:
        if not chunk.verified or not 0 <= chunk.start_char < chunk.end_char <= len(content):
            raise ValueError("unverified or invalid original chunk")
        if chunk.chunk_index in seen:
            continue
        seen.add(chunk.chunk_index)
        result.append(
            OriginalPassage(
                chunk_index=chunk.chunk_index,
                vector_id=chunk.vector_id,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                total_chars=len(content),
                body_hash=body_hash,
                range_hash=chunk.input_hash,
                content=content[chunk.start_char : chunk.end_char],
            )
        )
    return tuple(result)


def render_passages(passages: tuple[OriginalPassage, ...]) -> str:
    return "\n".join(f"[source chars {p.start_char}:{p.end_char}]\n{p.content}" for p in passages)
