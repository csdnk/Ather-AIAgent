"""Projection faults around the real Azure Milvus SDK, with test-owned collections."""

import asyncio
import copy
import json
import os

import pytest
from test_flows import app as app
from test_flows import context, drain, facts, save

from aether_agent_memory.recall.contracts.models import ProjectionRequest, VectorSearchRequest
from aether_agent_memory.remember.contracts.models import ProjectionTarget
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.storage.vectors import AzureVectors
from azure_test_runtime import owned


@pytest.fixture
def adapter(app):
    provider = AzureVectors(
        app.foundation.uow,
        app.foundation.identity,
        app.model_space,
        app.vectors.dimensions,
        namespace=owned().namespace,
        collection="adapter_faults",
        uri=os.environ["P3_TEST_MILVUS_URI"],
        token=os.environ["P3_TEST_MILVUS_TOKEN"],
        database=os.environ["P3_TEST_MILVUS_DATABASE"],
        ca_file=os.environ["P3_TEST_MILVUS_CA_FILE"],
        server_name=os.environ["P3_TEST_MILVUS_SERVER_NAME"],
    )
    owned().clients.append(provider)
    yield provider, provider.client


def remote_rows(provider):
    return provider.client.query(
        collection_name=provider.collection,
        filter='vector_id != ""',
        output_fields=["*"],
        limit=100,
        consistency_level="Strong",
        timeout=10,
    )


def request(app):
    receipt = save(app)
    drain(app)
    ref = facts(app, receipt)[0]
    rows = app.vectors.client.query(
        collection_name=app.vectors.collection,
        filter='target["memory"]["memory_id"] == ' + json.dumps(ref.memory_id),
        output_fields=["*"],
        limit=100,
        consistency_level="Strong",
        timeout=10,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["target"]["memory"] == ref.model_dump(mode="json")
    return ProjectionRequest(
        operation_id="project",
        target=ProjectionTarget.model_validate(row["target"]),
        vector=tuple(row["vector"]),
        deadline_at=context(app).deadline_at,
    )


def test_lost_upsert_response_reconciles_original_target(app, adapter, monkeypatch):
    provider, client = adapter
    req = request(app)
    original = client.upsert
    writes = []

    def lost(**kwargs):
        original(**kwargs)
        writes.extend(row["vector_id"] for row in kwargs["data"])
        raise OSError("controlled response loss after real Milvus commit")

    with monkeypatch.context() as patch:
        patch.setattr(client, "upsert", lost)
        result = asyncio.run(provider.project(context(app), req))
    assert result.state == "unknown"
    assert writes == [req.target.vector_id]
    assert len(remote_rows(provider)) == 1
    assert (
        asyncio.run(provider.inspect(context(app), req.target, req.operation_id)).state
        == "verified"
    )
    assert asyncio.run(provider.project(context(app), req)).state == "verified"
    assert len(remote_rows(provider)) == 1


def test_delete_requires_absence_and_tombstone_blocks_old_projection(app, adapter, monkeypatch):
    provider, client = adapter
    req = request(app)
    assert asyncio.run(provider.project(context(app), req)).state == "verified"
    with monkeypatch.context() as patch:
        patch.setattr(client, "delete", lambda **kwargs: None)
        assert asyncio.run(provider.delete(context(app), req.target, "delete")).state != "absent"
        assert len(remote_rows(provider)) == 1
    assert asyncio.run(provider.delete(context(app), req.target, "delete")).state == "absent"
    assert remote_rows(provider) == []
    with pytest.raises(FoundationError) as failure:
        asyncio.run(provider.project(context(app), req))
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"


def test_milvus_scope_filter_and_second_authorization_check(app, adapter, monkeypatch):
    provider, client = adapter
    req = request(app)
    assert asyncio.run(provider.project(context(app), req)).state == "verified"
    filters, original = [], client.search

    def observed(**kwargs):
        filters.append(kwargs["filter"])
        return original(**kwargs)

    monkeypatch.setattr(client, "search", observed)

    def search(user):
        ctx = context(app, user=user)
        return asyncio.run(
            provider.search(
                ctx,
                VectorSearchRequest(
                    selection=ScopeSelector(),
                    vector=req.vector,
                    model_space=app.model_space,
                    deadline_at=ctx.deadline_at,
                ),
            )
        )

    assert len(search("alice").candidates) == 1
    assert not search("bob").candidates
    assert not search("carol").candidates
    assert 'tenant_id == "t2"' in filters[-1]
    assert 'user_id == "carol"' in filters[-1]
    assert "model_space ==" in filters[-1]


@pytest.mark.parametrize("source", ["working", "long_term"])
def test_milvus_source_predicate_filters_before_top_k_and_keeps_legacy_long_term(
    app, adapter, source
):
    provider, client = adapter
    req = request(app)
    asyncio.run(provider.prepare(context(app)))
    rows = []
    for name, memory_source, score in (
        ("working_memory", "working", 0.5),
        ("legacy_memory", None, 1.0),
    ):
        target = copy.deepcopy(req.target.model_dump(mode="json"))
        target["memory"]["memory_id"] = name
        if memory_source:
            target["memory_source"] = memory_source
        else:
            target.pop("memory_source", None)
        rows.append(
            {
                "vector_id": name,
                "target": target,
                "model_space": app.model_space,
                "vector": [v * score for v in req.vector],
                **{key: value or "" for key, value in req.target.memory.scope.model_dump().items()},
            }
        )
    client.upsert(collection_name=provider.collection, data=rows, timeout=10)
    raw = client.search(
        collection_name=provider.collection,
        data=[list(req.vector)],
        anns_field="vector",
        filter='tenant_id == "t1" and user_id == "alice"',
        limit=1,
        output_fields=["target"],
        search_params={"metric_type": "IP"},
        consistency_level="Strong",
        timeout=10,
    )
    assert raw[0][0]["entity"]["target"]["memory"]["memory_id"] == "legacy_memory"
    ctx = context(app)
    result = asyncio.run(
        provider.search(
            ctx,
            VectorSearchRequest(
                selection=ScopeSelector(),
                vector=req.vector,
                model_space=app.model_space,
                memory_source=source,
                limit=1,
                deadline_at=ctx.deadline_at,
            ),
        )
    )
    expected = "working_memory" if source == "working" else "legacy_memory"
    assert [c.target.memory.memory_id for c in result.candidates] == [expected]


def test_changed_vector_cannot_overwrite_projection(app, adapter):
    provider, _ = adapter
    req = request(app)
    assert asyncio.run(provider.project(context(app), req)).state == "verified"
    changed = req.model_copy(update={"vector": tuple(-v for v in req.vector)})
    with pytest.raises(FoundationError) as failure:
        asyncio.run(provider.project(context(app), changed))
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"
    assert asyncio.run(provider.inspect(context(app), req.target, "original")).state == "verified"


def test_dimension_mismatch_rejected_before_remote_write(app, adapter):
    provider, client = adapter
    req = request(app).model_copy(update={"vector": (1.0,)})
    with pytest.raises(FoundationError):
        asyncio.run(provider.project(context(app), req))
    assert not client.has_collection(collection_name=provider.collection, timeout=10)


def test_existing_collection_with_wrong_shape_is_rejected(app, adapter):
    provider, client = adapter
    client.create_collection(collection_name=provider.collection, dimension=999, timeout=10)
    with pytest.raises(FoundationError) as failure:
        asyncio.run(provider.prepare(context(app)))
    assert failure.value.code == "CONTRACT_VIOLATION"
    assert not provider.prepared
