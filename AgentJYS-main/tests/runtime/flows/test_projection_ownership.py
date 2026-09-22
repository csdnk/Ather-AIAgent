"""Ownership and capability boundaries for Remember writes and Recall searches."""

import asyncio
import inspect

from test_flows import app as app
from test_flows import context, drain, facts, save

from aether_agent_memory.recall.basic.vector_search import MilvusVectorSearch, SQLiteVectorSearch
from aether_agent_memory.recall.contracts.foundation import (
    ChunkProjectionRequest as LegacyChunkRequest,
)
from aether_agent_memory.recall.contracts.models import (
    ProjectionTarget as LegacyProjectionTarget,
)
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.recall.contracts.ports import GenerationSearchPort, VectorSearchPort
from aether_agent_memory.remember.basic.projection import MilvusProjection, SQLiteProjection
from aether_agent_memory.remember.contracts.foundation import ChunkProjectionRequest
from aether_agent_memory.remember.contracts.models import DeleteRequest, ProjectionTarget
from aether_agent_memory.remember.contracts.ports import GenerationProjectionPort, ProjectionPort
from aether_agent_memory.runtime.contracts.models import ScopeSelector


def test_host_limits_capabilities_through_save_recall_and_cleanup(app, monkeypatch):
    assert app.remember.projections is app.projections
    assert app.recall.vectors is app.vector_search
    assert not hasattr(app.projections, "search")
    for method in ("project", "inspect", "delete"):
        assert not hasattr(app.vector_search, method)
        assert not hasattr(app.recall.sources.vectors, method)

    calls = []
    for method in ("project", "inspect", "delete", "search"):
        original = getattr(app.vectors, method)

        async def record(*args, _method=method, _original=original, **kwargs):
            calls.append(_method)
            return await _original(*args, **kwargs)

        monkeypatch.setattr(app.vectors, method, record)

    receipt = save(app, text="预算为30万元")
    drain(app)
    memory = facts(app, receipt)[0]
    assert "project" in calls and "inspect" in calls
    assert "search" not in calls

    pack = asyncio.run(
        app.recall.recall(
            context(app),
            RecallRequest(query="预算", selection=ScopeSelector(), sources="long_term"),
        )
    )
    assert "search" in calls and "30" in pack.rendered_context
    snapshot = app.remember.get(context(app), memory.memory_id)
    app.remember.delete(
        context(app),
        memory.memory_id,
        DeleteRequest(expected_revision=snapshot.revision, reason="ownership regression"),
    )
    drain(app)
    assert "delete" in calls


def test_projection_types_have_one_canonical_owner_and_legacy_aliases():
    assert LegacyProjectionTarget is ProjectionTarget
    assert LegacyChunkRequest is ChunkProjectionRequest
    assert ProjectionTarget.__module__ == "aether_agent_memory.remember.contracts.models"
    assert ChunkProjectionRequest.__module__ == "aether_agent_memory.remember.contracts.foundation"
    # Contracts must stay narrow even though old aggregate imports remain usable.
    for write, read in (
        (ProjectionPort, VectorSearchPort),
        (GenerationProjectionPort, GenerationSearchPort),
    ):
        assert not hasattr(write, "search")
        assert hasattr(read, "search")
        for method in ("project", "inspect", "delete"):
            assert not hasattr(read, method)


def test_sqlite_and_milvus_implementations_are_split_by_business_owner():
    for writer, reader in (
        (SQLiteProjection, SQLiteVectorSearch),
        (MilvusProjection, MilvusVectorSearch),
    ):
        assert not hasattr(writer, "search")
        assert inspect.getmodule(writer.project).__name__.startswith(
            "aether_agent_memory.remember."
        )
        assert inspect.getmodule(reader.search).__name__.startswith("aether_agent_memory.recall.")
        for method in ("project", "inspect", "delete"):
            assert not hasattr(reader, method)
