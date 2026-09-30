"""Milvus protocol fault tests; these do not claim a running Milvus server."""

import asyncio
import copy
import json
import re

import pytest
from test_flows import app as app
from test_flows import context, drain, facts, save

from aether_agent_memory.recall.basic.adapters import projection_target
from aether_agent_memory.recall.basic.milvus import MilvusVectors
from aether_agent_memory.recall.contracts.models import ProjectionRequest, VectorSearchRequest
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError


class Client:
    def __init__(self):
        self.rows = {}
        self.lost_response = False
        self.ignore_delete = False
        self.filters = []

    def upsert(self, *, data, **kwargs):
        for row in data:
            self.rows[row["vector_id"]] = copy.deepcopy(row)
        if self.lost_response:
            raise ConnectionError("response lost after apply")

    def query(self, *, filter, **kwargs):
        key = json.loads(filter.split(" == ", 1)[1])
        return [self.rows[key]] if key in self.rows else []

    def search(self, *, data, filter, limit, **kwargs):
        self.filters.append(filter)
        conditions = [
            (k, json.loads(v)) for k, v in re.findall(r'(\w+) == ("(?:[^"\\]|\\.)*")', filter)
        ]
        rows = [r for r in self.rows.values() if all(r[k] == v for k, v in conditions)]
        scored = sorted(
            [(sum(a * b for a, b in zip(data[0], r["vector"], strict=True)), r) for r in rows],
            key=lambda pair: -pair[0],
        )
        return [[{"distance": score, "entity": row} for score, row in scored[:limit]]]

    def delete(self, *, ids, **kwargs):
        if not self.ignore_delete:
            for key in ids:
                self.rows.pop(key, None)

    def close(self):
        pass


@pytest.fixture
def adapter(app):
    client = Client()
    provider = MilvusVectors(
        app.foundation.uow,
        app.foundation.identity,
        app.model_space,
        app.vectors.dimensions,
        uri="test",
        client=client,
    )
    provider.prepared = True
    yield provider, client
    provider.close()


def request(app):
    receipt = save(app)
    drain(app)
    memory = app.remember.get(context(app), facts(app, receipt)[0].memory_id)
    target = projection_target(memory.ref, memory.content_hash, app.model_space)
    with app.foundation.uow.transaction() as tx:
        row = tx.read("recall_vectors", target.vector_id)
    return ProjectionRequest(
        operation_id="project",
        target=target,
        vector=tuple(row["vector"]),
        deadline_at=context(app).deadline_at,
    )


def test_lost_upsert_response_reconciles_original_target(app, adapter):
    provider, client = adapter
    req = request(app)
    client.lost_response = True
    ctx = context(app)
    result = asyncio.run(provider.project(ctx, req))
    assert result.state == "unknown"
    assert len(client.rows) == 1
    result = asyncio.run(provider.inspect(context(app), req.target, req.operation_id))
    assert result.state == "verified"
    client.lost_response = False
    assert asyncio.run(provider.project(context(app), req)).state == "verified"
    assert len(client.rows) == 1


def test_delete_requires_absence_and_tombstone_blocks_old_projection(app, adapter):
    provider, client = adapter
    req = request(app)
    asyncio.run(provider.project(context(app), req))
    client.ignore_delete = True
    assert asyncio.run(provider.delete(context(app), req.target, "delete")).state != "absent"
    client.ignore_delete = False
    assert asyncio.run(provider.delete(context(app), req.target, "delete")).state == "absent"
    with pytest.raises(FoundationError) as failure:
        asyncio.run(provider.project(context(app), req))
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"


def test_milvus_scope_filter_and_second_authorization_check(app, adapter):
    provider, client = adapter
    req = request(app)
    asyncio.run(provider.project(context(app), req))

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
    assert 'tenant_id == "t2"' in client.filters[-1]
    assert 'user_id == "carol"' in client.filters[-1]
    assert "model_space ==" in client.filters[-1]


@pytest.mark.parametrize("source", ["working", "long_term"])
def test_milvus_source_predicate_filters_before_top_k_and_keeps_legacy_long_term(
    app, adapter, source
):
    provider, client = adapter
    req = request(app)
    original = client.search
    expected = "working_memory" if source == "working" else "legacy_memory"
    for name, memory_source, score in (
        ("working_memory", "working", 0.5),
        ("legacy_memory", None, 1.0),
    ):
        target = req.target.model_dump(mode="json")
        target["memory"]["memory_id"] = name
        if memory_source:
            target["memory_source"] = memory_source
        else:
            target.pop("memory_source", None)
        client.rows[name] = {
            **req.target.memory.scope.model_dump(mode="json"),
            "target": target,
            "model_space": app.model_space,
            "vector": [v * score for v in req.vector],
        }

    def scoped_search(*, data, filter, limit, **kwargs):
        # Model the external engine's JSON predicate before its candidate limit.
        rows = client.rows
        if 'target["memory_source"] == "working"' in filter:
            client.rows = {
                k: r for k, r in rows.items() if r["target"].get("memory_source") == "working"
            }
        elif 'not exists target["memory_source"]' in filter:
            client.rows = {
                k: r
                for k, r in rows.items()
                if r["target"].get("memory_source", "long_term") == "long_term"
            }
        try:
            return original(data=data, filter=filter, limit=limit, **kwargs)
        finally:
            client.rows = rows

    client.search = scoped_search
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
    assert [c.target.memory.memory_id for c in result.candidates] == [expected]


def test_changed_vector_cannot_overwrite_projection(app, adapter):
    provider, _ = adapter
    req = request(app)
    asyncio.run(provider.project(context(app), req))
    changed = req.model_copy(update={"vector": tuple(-v for v in req.vector)})
    with pytest.raises(FoundationError) as failure:
        asyncio.run(provider.project(context(app), changed))
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"


def test_dimension_mismatch_rejected_before_remote_write(app, adapter):
    provider, client = adapter
    req = request(app).model_copy(update={"vector": (1.0,)})
    with pytest.raises(FoundationError):
        asyncio.run(provider.project(context(app), req))
    assert not client.rows


def test_existing_collection_with_wrong_shape_is_rejected(app, adapter, monkeypatch):
    provider, client = adapter
    provider.prepared = False
    monkeypatch.setattr(client, "has_collection", lambda **kwargs: True, raising=False)
    monkeypatch.setattr(
        client,
        "describe_collection",
        lambda **kwargs: {"fields": [{"name": "vector", "params": {"dim": 999}}]},
        raising=False,
    )
    with pytest.raises(FoundationError) as failure:
        asyncio.run(provider.prepare(context(app)))
    assert failure.value.code == "CONTRACT_VIOLATION"
    assert not provider.prepared
