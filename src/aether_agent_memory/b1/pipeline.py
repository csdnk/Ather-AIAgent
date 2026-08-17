from time import perf_counter
from typing import Protocol, runtime_checkable

from aether_agent_memory.b1.models import (
    EmbeddingRecord,
    EmbeddingRequest,
    EmbeddingResult,
    ProcessingStatus,
    TextChunk,
)
from aether_agent_memory.interfaces.embedding import EmbeddingClient


@runtime_checkable
class VectorSink(Protocol):
    async def upsert(self, records: list[EmbeddingRecord]) -> None: ...


class InMemoryVectorSink:
    def __init__(self) -> None:
        self._records: dict[str, EmbeddingRecord] = {}

    async def upsert(self, records: list[EmbeddingRecord]) -> None:
        for record in records:
            self._records[record.chunk_id] = record

    async def delete_by_object_id(self, object_id: str) -> int:
        chunk_ids = [
            chunk_id
            for chunk_id, record in self._records.items()
            if record.object_id == object_id
        ]
        for chunk_id in chunk_ids:
            del self._records[chunk_id]
        return len(chunk_ids)

    @property
    def records(self) -> list[EmbeddingRecord]:
        return list(self._records.values())


class TextChunker:
    def __init__(self, *, max_chars: int = 1200, overlap_chars: int = 120) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        if overlap_chars < 0 or overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be in [0, max_chars)")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk(self, request: EmbeddingRequest) -> list[TextChunk]:
        text = request.text.strip()
        if not text:
            return []
        source_id = (
            request.source_id
            or request.doc_id
            or request.memory_id
            or request.object_id
            or request.request_id
        )
        explicit_chunk_id = request.metadata.get("chunk_id")
        chunk_key = (
            str(explicit_chunk_id)
            if explicit_chunk_id is not None
            else request.object_id or request.memory_id or request.doc_id or source_id
        )
        chunks: list[TextChunk] = []
        start = 0
        index = 0
        while start < len(text):
            hard_end = min(start + self.max_chars, len(text))
            end = self._natural_boundary(text, start, hard_end)
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(
                    TextChunk(
                        chunk_id=f"{chunk_key}:{index:04d}",
                        source_id=source_id,
                        text=chunk_text,
                        index=index,
                        start_char=start,
                        end_char=end,
                    )
                )
                index += 1
            if end >= len(text):
                break
            start = max(end - self.overlap_chars, start + 1)
        return chunks

    @staticmethod
    def _natural_boundary(text: str, start: int, hard_end: int) -> int:
        if hard_end >= len(text):
            return len(text)
        search_start = start + (hard_end - start) // 2
        best = max(
            text.rfind(mark, search_start, hard_end) for mark in ("\n", "。", "！", "？", ".", " ")
        )
        return best + 1 if best >= search_start else hard_end


class EmbeddingPipeline:
    def __init__(
        self,
        *,
        embedder: EmbeddingClient,
        sink: VectorSink,
        chunker: TextChunker | None = None,
        model_name: str = "mock",
        fail_open: bool = True,
    ) -> None:
        self.embedder = embedder
        self.sink = sink
        self.chunker = chunker or TextChunker()
        self.model_name = model_name
        self.fail_open = fail_open

    async def process(self, request: EmbeddingRequest) -> EmbeddingResult:
        started = perf_counter()
        source_id = (
            request.source_id
            or request.doc_id
            or request.memory_id
            or request.object_id
            or request.request_id
        )
        try:
            chunks = self.chunker.chunk(request)
            if not chunks:
                raise ValueError("input text is empty")
            vectors = await self.embedder.embed([chunk.text for chunk in chunks])
            self._validate_vectors(chunks, vectors)
            records = [
                EmbeddingRecord(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    source_id=source_id,
                    object_id=request.object_id,
                    chunk_id=chunk.chunk_id,
                    chunk_text=chunk.text,
                    vector=vector,
                    embedding_model=self.model_name,
                    metadata={
                        **request.metadata,
                        "chunk_index": chunk.index,
                        "start_char": chunk.start_char,
                        "end_char": chunk.end_char,
                        "source_type": request.source_type.value,
                    },
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
            await self.sink.upsert(records)
            return EmbeddingResult(
                request_id=request.request_id,
                trace_id=request.trace_id,
                source_id=source_id,
                status=ProcessingStatus.SUCCESS,
                records=records,
                latency_ms=(perf_counter() - started) * 1000,
            )
        except Exception as exc:
            if not self.fail_open:
                raise
            return EmbeddingResult(
                request_id=request.request_id,
                trace_id=request.trace_id,
                source_id=source_id,
                status=ProcessingStatus.FAILED,
                latency_ms=(perf_counter() - started) * 1000,
                error_code=type(exc).__name__.upper(),
                error_message=str(exc),
            )

    @staticmethod
    def _validate_vectors(chunks: list[TextChunk], vectors: list[list[float]]) -> None:
        if len(vectors) != len(chunks):
            raise ValueError("embedding result count does not match chunk count")
        if not vectors or not vectors[0]:
            raise ValueError("embedding vectors are empty")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise ValueError("embedding dimensions are inconsistent")
