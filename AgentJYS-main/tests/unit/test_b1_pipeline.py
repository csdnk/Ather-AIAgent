import pytest

from aether_agent_memory.b1 import (
    EmbeddingPipeline,
    EmbeddingRequest,
    InMemoryVectorSink,
    ProcessingStatus,
    TextChunker,
)
from aether_agent_memory.core.enums import SourceType
from aether_agent_memory.mocks.embedding import MockEmbeddingClient


@pytest.mark.unit
async def test_pipeline_chunks_embeds_and_upserts_with_traceability() -> None:
    sink = InMemoryVectorSink()
    pipeline = EmbeddingPipeline(
        embedder=MockEmbeddingClient(dim=16),
        sink=sink,
        chunker=TextChunker(max_chars=12, overlap_chars=2),
        model_name="mock-16",
    )
    request = EmbeddingRequest(
        text="第一段需要向量化。第二段也需要向量化。",
        source_type=SourceType.DOCUMENT,
        source_id="manual-1",
        object_id="obj-1",
        tenant_id="tenant-a",
    )

    result = await pipeline.process(request)

    assert result.status == ProcessingStatus.SUCCESS
    assert len(result.records) >= 2
    assert result.records == sink.records
    assert all(len(record.vector) == 16 for record in result.records)
    assert all(record.source_id == "manual-1" for record in result.records)
    assert all(record.trace_id == request.trace_id for record in result.records)
    assert len({record.chunk_id for record in result.records}) == len(result.records)


@pytest.mark.unit
async def test_pipeline_fail_open_returns_structured_error() -> None:
    pipeline = EmbeddingPipeline(
        embedder=MockEmbeddingClient(),
        sink=InMemoryVectorSink(),
        fail_open=True,
    )
    result = await pipeline.process(EmbeddingRequest(text="   ", source_type=SourceType.DOCUMENT))
    assert result.status == ProcessingStatus.FAILED
    assert result.error_code == "VALUEERROR"
    assert result.records == []


@pytest.mark.unit
async def test_pipeline_uses_explicit_chunk_id_for_prechunked_text() -> None:
    sink = InMemoryVectorSink()
    pipeline = EmbeddingPipeline(
        embedder=MockEmbeddingClient(dim=8),
        sink=sink,
    )

    result = await pipeline.process(
        EmbeddingRequest(
            text="pre-chunked text",
            source_type=SourceType.DOCUMENT,
            object_id="object-v3",
            metadata={"chunk_id": "chunk-007"},
        )
    )

    assert result.records[0].object_id == "object-v3"
    assert result.records[0].chunk_id == "chunk-007:0000"
    assert await sink.delete_by_object_id("object-v3") == 1
    assert sink.records == []


@pytest.mark.unit
def test_chunker_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        TextChunker(max_chars=100, overlap_chars=100)
