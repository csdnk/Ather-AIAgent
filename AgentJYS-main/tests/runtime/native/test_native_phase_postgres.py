"""Adapter phase boundaries with real PostgreSQL and controlled model output."""

from contextlib import contextmanager

from test_native_adapter import Backend
from test_postgres_observability import dsns as dsns
from test_postgres_observability import open_host, people

from aether_agent_memory.recall.contracts.models import EmbeddingRequest
from aether_agent_memory.recall.embedding import p3


async def test_adapter_measures_preparation_and_final_authorization(tmp_path, dsns, monkeypatch):
    monkeypatch.setattr(p3, "NativeEmbeddingBackend", Backend)
    host = open_host(tmp_path, dsns)
    people(host)
    measured = []

    @contextmanager
    def stage(name):
        measured.append(name)
        yield

    monkeypatch.setattr(p3, "measure_stage", stage, raising=False)
    embedding = p3.NativeP3Embedding(host.uow, host.identity)
    try:
        ctx = host.identity.context("alice")
        result = await embedding.embed(
            ctx,
            EmbeddingRequest(
                operation_id="phase-check",
                usage="query",
                texts=("phase test",),
                model_space=embedding.model_space,
                deadline_at=ctx.deadline_at,
            ),
        )
        assert len(result.items) == 1
        assert measured == ["embedding_prepare", "embedding_finalize"]
    finally:
        embedding.close()
        host.close()
