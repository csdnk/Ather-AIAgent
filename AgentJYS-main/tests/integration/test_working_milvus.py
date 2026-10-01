"""Explicit opt-in acceptance: native BGE, official Lite gRPC, public P3 HTTP.

Fault doubles only lose/delay a response around a real SDK/embedding operation.
SQLite still owns P3 metadata and bodies; every vector assertion uses real Lite.
"""

import asyncio
import hashlib
import json
import math
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.milvus_support import PersistentMilvus, evidence
from tests.integration.test_continuous_service import configuration as configuration
from tests.integration.test_continuous_service import eventually, headers, provision

from aether_agent_memory.recall.contracts.models import VectorSearchRequest
from aether_agent_memory.remember.contracts.models import ProjectionRequest, ProjectionTarget
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.flows.application import Service
from aether_agent_memory.runtime.flows.vector_adapters import MilvusVectors

pytestmark = pytest.mark.skipif(
    os.environ.get("P3_TEST_MILVUS_LITE") != "1",
    reason="explicit P3_TEST_MILVUS_LITE=1 required; use scripts/p3/validate_working_milvus.py",
)

TOPICS = (
    ("用户喝咖啡时不加糖。", "早上的咖啡需要放蔗糖吗？"),
    ("用户对虾和蟹过敏，不能食用海鲜。", "给他点餐能选海鲜拼盘吗？"),
    ("用户长途出行优先选择高铁。", "跨城出差优先订火车还是机票？"),
)


@pytest.fixture
def milvus(tmp_path):
    server = PersistentMilvus(tmp_path / "milvus")
    try:
        server.start()
        yield server
    finally:
        server.stop()


@pytest.fixture
def real_config(configuration, milvus, tmp_path):
    native = os.environ.get("P3_TEST_NATIVE_CONFIG")
    assert native and Path(native).is_file(), "real native BGE configuration is mandatory"
    profile = json.loads(
        (Path(__file__).resolve().parents[2] / "configs/recall.milvus-lite.json").read_text()
    )
    profile.update(milvus_uri=milvus.uri, max_items=1, candidate_limit=3)
    recall = tmp_path / "recall.json"
    recall.write_text(json.dumps(profile), "utf-8")
    return configuration.model_copy(
        update={
            "embedding_profile": "native",
            "embedding_config": Path(native),
            "recall_config": recall,
        }
    )


def source(operation):
    return {
        "kind": "conversation",
        "external_id": operation,
        "external_version": "1",
        "occurred_at": "2026-09-30T00:00:00.000Z",
    }


def save(client, content, operation, *, session="s1", user="alice"):
    response = client.post(
        "/p3/remember",
        headers=headers(user, operation),
        json={
            "source": source(operation),
            "selection": {"session_id": session},
            "content": {"kind": "text", "text": content},
        },
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["saved"] is True
    assert receipt["phase"] in {"saved", "processing", "ready"}
    return receipt["memories"][0]["memory_id"]


def snapshot(client, mid):
    response = client.get(f"/p3/remember/{mid}", headers=headers())
    assert response.status_code == 200, response.text
    return response.json()


def ready(client, mid, version=1):
    def check():
        item = snapshot(client, mid)
        return (
            item
            if item["projection_state"] == "ready" and item["ref"]["version"] == version
            else None
        )

    return eventually(check)


def recall(client, query, *, session="s1", user="alice", sources="working"):
    response = client.post(
        "/p3/recall",
        headers=headers(user),
        json={
            "query": query,
            "selection": {"session_id": session},
            "sources": sources,
            "token_budget": 1000,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def contents(pack):
    return [item["content"] for group in pack["groups"] for item in group["items"]]


def db_rows(service, mid=None):
    vectors = service.runtime.vectors
    expression = (
        'vector_id != ""' if mid is None else 'target["memory"]["memory_id"] == ' + json.dumps(mid)
    )
    return vectors.client.query(
        collection_name=vectors.collection,
        filter=expression,
        output_fields=["*"],
        limit=100,
        consistency_level="Strong",
        timeout=10,
    )


def current_vector(service, item):
    rows = [
        r
        for r in db_rows(service, item["ref"]["memory_id"])
        if r["target"]["memory"]["version"] == item["ref"]["version"]
    ]
    assert len(rows) == 1
    row = rows[0]
    assert len(row["vector"]) == 512 and all(math.isfinite(v) for v in row["vector"])
    assert sum(v * v for v in row["vector"]) == pytest.approx(1, abs=1e-4)
    assert row["target"]["memory_source"] == "working"
    assert row["target"]["generation"]
    assert row["target"]["body_hash"] == hashlib.sha256(item["content"].encode()).hexdigest()
    assert row["target"]["memory"] == item["ref"]
    return row


def correct(client, item, content, operation):
    response = client.post(
        f"/p3/remember/{item['ref']['memory_id']}/correct",
        headers=headers(),
        json={
            "expected_version": item["ref"]["version"],
            "content": content,
            "reason": "acceptance correction",
            "source": source(operation),
        },
    )
    assert response.status_code == 200, response.text
    return ready(client, item["ref"]["memory_id"], item["ref"]["version"] + 1)


def delete(client, item):
    response = client.post(
        f"/p3/remember/{item['ref']['memory_id']}/delete",
        headers=headers(),
        json={
            "expected_revision": item["object_revision"],
            "reason": "acceptance deletion",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["blocked"]


def test_real_working_semantics_scope_and_source_topk(real_config):
    service = Service(real_config)
    observations = []
    with TestClient(service.app()) as client:
        ids = [save(client, text, f"topic_{i}") for i, (text, _) in enumerate(TOPICS)]
        items = [ready(client, mid) for mid in ids]
        assert service.runtime.native_embedding.space.dimensions == 512
        rows = [current_vector(service, item) for item in items]
        for (text, query), mid in zip(TOPICS, ids, strict=True):
            started = time.monotonic()
            result = recall(client, query)
            assert contents(result) == [text]
            assert result["groups"][0]["items"][0]["memory"]["memory_id"] == mid
            observations.append(
                {"query": query, "result": result, "seconds": time.monotonic() - started}
            )
        for user, session in [("alice", "s2"), ("bob", "s1"), ("eve", "s1")]:
            assert recall(client, TOPICS[0][1], user=user, session=session)["outcome"] == "empty"

        # Existing long-term pipeline also writes actual native vectors.
        def long_term_ready():
            response = client.get("/p3/memories", headers=headers()).json()
            return [
                m
                for m in response["items"]
                if m["kind"] != "working" and m["projection_state"] == "ready"
            ]

        eventually(long_term_ready)
        assert not set(ids).intersection(
            item["memory"]["memory_id"]
            for group in recall(client, TOPICS[0][1], sources="long_term")["groups"]
            for item in group["items"]
        )

        # Project two actual pipeline-produced vectors to a separate real collection.
        # Distinct topics guarantee each limit=1 query has a stronger distractor
        # from the other source, without tying identical Working/fact vectors.
        def rail_fact():
            for memory in long_term_ready():
                item = snapshot(client, memory["ref"]["memory_id"])
                if "高铁" in item["content"]:
                    return db_rows(service, item["ref"]["memory_id"])[0]
            return None

        rail = eventually(rail_fact)
        backend = service.runtime.vectors
        vectors = MilvusVectors(
            backend.uow,
            backend.identity,
            backend.model_space,
            backend.dimensions,
            uri=json.loads(real_config.recall_config.read_text())["milvus_uri"],
            collection="source_topk",
            serialize_writes=True,
        )
        try:
            for row in [rows[0], rail]:
                ctx = backend.identity.context("alice", timeout_seconds=30)
                projected = asyncio.run(
                    vectors.project(
                        ctx,
                        ProjectionRequest(
                            operation_id=ctx.operation_id,
                            target=ProjectionTarget.model_validate(row["target"]),
                            vector=tuple(row["vector"]),
                            deadline_at=ctx.deadline_at,
                        ),
                    )
                )
                assert projected.state == "verified"
            topk = source_topk(vectors, rows[0], rail)
        finally:
            vectors.close()
        evidence(
            "semantics-scope-source", {"observations": observations, "vectors": rows, "topk": topk}
        )


def source_topk(vectors, coffee, rail):
    topk = []
    for requested, query_row in [("working", rail), ("long_term", coffee)]:
        raw = vectors.client.search(
            collection_name=vectors.collection,
            data=[query_row["vector"]],
            anns_field="vector",
            filter='session_id == "s1"',
            limit=1,
            output_fields=["target"],
            search_params={"metric_type": "IP"},
            timeout=10,
        )
        assert raw[0][0]["entity"]["target"]["memory_source"] != requested
        ctx = vectors.identity.context("alice", timeout_seconds=30)
        result = asyncio.run(
            vectors.search(
                ctx,
                VectorSearchRequest(
                    selection=ScopeSelector(session_id="s1"),
                    memory_source=requested,
                    vector=tuple(query_row["vector"]),
                    model_space=vectors.model_space,
                    limit=1,
                    deadline_at=ctx.deadline_at,
                ),
            )
        )
        assert len(result.candidates) == 1
        assert result.candidates[0].target.memory_source == requested
        topk.append({"unfiltered": raw, "filtered": result.model_dump(mode="json")})
    return topk


def test_real_working_database_and_service_restart(real_config, milvus):
    service = Service(real_config)
    with TestClient(service.app()) as client:
        mid = save(client, TOPICS[0][0], "persistent")
        row = current_vector(service, ready(client, mid))
        old = recall(client, TOPICS[0][1])
        assert contents(old) == [TOPICS[0][0]]
    first_pid = milvus.process.pid
    milvus.stop()
    milvus.start()
    assert first_pid != milvus.process.pid
    from pymilvus import MilvusClient

    fresh = MilvusClient(uri=milvus.uri, timeout=10)
    try:
        fresh.load_collection("p3_memories", timeout=10)
        reopened = fresh.get(
            collection_name="p3_memories", ids=[row["vector_id"]], output_fields=["*"]
        )
        assert len(reopened) == 1
        assert reopened[0]["target"] == row["target"]
        assert reopened[0]["vector"] == pytest.approx(row["vector"])
    finally:
        fresh.close()
    with TestClient(Service(real_config).app()) as client:
        assert contents(recall(client, TOPICS[0][1])) == [TOPICS[0][0]]
        replay = client.get(f"/p3/recalls/{old['recall_id']}/result", headers=headers())
        assert replay.status_code == 200, replay.text
        assert contents(replay.json()) == [TOPICS[0][0]]
        assert (
            client.get(f"/p3/recalls/{old['recall_id']}/result", headers=headers("eve")).status_code
            == 403
        )
        correct(client, ready(client, mid), "用户现在喝无糖茶。", "restart_correction")
        assert (
            client.get(f"/p3/recalls/{old['recall_id']}/result", headers=headers()).status_code
            == 410
        )
    evidence("persistence", {"processes": milvus.history, "reopened": reopened})


def test_real_working_share_correct_delete_and_expiry(real_config):
    service = Service(real_config)
    with TestClient(service.app()) as client:
        mid = save(client, TOPICS[0][0], "lifecycle")
        item = ready(client, mid)
        assert item["expires_at"] is None
        grant = {
            "grant_id": "share_working",
            "grantee_id": "bob",
            "grantee_tenant_id": "t1",
            "revision": 1,
            "permissions": ["memory:read"],
            "resource": {
                "owner": "remember",
                "object_type": "memory",
                "object_id": mid,
                "scope": item["ref"]["scope"],
            },
        }
        provision(real_config.identity_file, revision=2, grants=[grant])
        eventually(lambda: service.identity_hash == real_config.identity_file.read_bytes())
        shared = recall(client, TOPICS[0][1], user="bob")
        assert contents(shared) == [TOPICS[0][0]]
        provision(real_config.identity_file, revision=3)
        eventually(lambda: service.identity_hash == real_config.identity_file.read_bytes())
        assert client.get(
            f"/p3/recalls/{shared['recall_id']}/result", headers=headers("bob")
        ).status_code in {403, 410}
        assert recall(client, TOPICS[0][1], user="bob")["outcome"] == "empty"
        old = recall(client, TOPICS[0][1])
        current = correct(client, item, "用户现在改喝无糖茶。", "lifecycle_correction")
        current_vector(service, current)
        assert (
            client.get(f"/p3/recalls/{old['recall_id']}/result", headers=headers()).status_code
            == 410
        )
        new = recall(client, "茶里面要放糖吗？")
        assert contents(new) == ["用户现在改喝无糖茶。"]
        delete(client, snapshot(client, mid))
        assert recall(client, "茶里面要放糖吗？")["outcome"] == "empty"
        assert (
            client.get(f"/p3/recalls/{new['recall_id']}/result", headers=headers()).status_code
            == 410
        )
        expiry_mid = save(client, TOPICS[2][0], "expiry", session="ttl")
        expiring = ready(client, expiry_mid)
        expiry = (
            (datetime.now(UTC) + timedelta(seconds=2))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        response = client.post(
            f"/p3/remember/{expiry_mid}/retention",
            headers=headers(),
            json={
                "expected_version": 1,
                "expected_object_revision": expiring["object_revision"],
                "completed": True,
                "expires_at": expiry,
                "reason": "explicit acceptance TTL",
            },
        )
        assert response.status_code == 200, response.text
        ttl_pack = recall(client, TOPICS[2][1], session="ttl")
        assert contents(ttl_pack) == [TOPICS[2][0]]
        eventually(
            lambda: datetime.now(UTC) >= datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        )
        assert recall(client, TOPICS[2][1], session="ttl")["outcome"] == "empty"
        assert (
            client.get(f"/p3/recalls/{ttl_pack['recall_id']}/result", headers=headers()).status_code
            == 410
        )
        evidence(
            "lifecycle",
            {
                "shared": shared,
                "corrected": current,
                "expiry": expiry,
                "retention": response.json(),
            },
        )


def test_real_working_ambiguous_write_recovers_once(real_config, monkeypatch):
    service = Service(real_config)
    real_upsert = service.runtime.vectors.client.upsert
    lost = threading.Event()
    writes = []

    def lose_response(**kwargs):
        result = real_upsert(**kwargs)
        for row in kwargs["data"]:
            writes.append(row["vector_id"])
        if not lost.is_set() and kwargs["data"][0]["target"]["memory_source"] == "working":
            lost.set()
            raise OSError("controlled response loss AFTER real Milvus commit")
        return result

    monkeypatch.setattr(service.runtime.vectors.client, "upsert", lose_response)
    with TestClient(service.app()) as client:
        mid = save(client, TOPICS[0][0], "lost_response")
        eventually(lost.is_set)
        item = ready(client, mid)
        row = current_vector(service, item)
        assert writes.count(row["vector_id"]) == 1
        assert save(client, TOPICS[0][0], "lost_response") == mid
        assert contents(recall(client, TOPICS[0][1])) == [TOPICS[0][0]]
        assert len([r for r in db_rows(service, mid) if r["target"]["memory"]["version"] == 1]) == 1
        evidence("ambiguous-write", {"writes": writes, "row": row, "memory": item})


def test_real_working_late_projection_cannot_resurrect_deleted_memory(real_config, monkeypatch):
    service = Service(real_config)
    embedding = service.runtime.remember.embedding
    real_embed = embedding.embed
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()

    async def held_embed(ctx, request):
        result = await real_embed(ctx, request)
        if request.usage == "passage" and not entered.is_set():
            entered.set()
            try:
                assert await asyncio.to_thread(release.wait, 30), "test failed to release embedding"
            finally:
                completed.set()
        return result

    monkeypatch.setattr(embedding, "embed", held_embed)
    with TestClient(service.app()) as client:
        try:
            mid = save(client, TOPICS[0][0], "late_delete")
            eventually(entered.is_set)
            item = snapshot(client, mid)
            assert item["projection_state"] != "ready"
            delete(client, item)
        finally:
            release.set()
        eventually(completed.is_set)

        def projection_settled():
            response = client.get("/p3/tasks", headers=headers(), params={"limit": 100})
            assert response.status_code == 200, response.text
            tasks = [
                task
                for task in response.json()["items"]
                if task["kind"] == "remember.project" and task["subject"]["object_id"] == mid
            ]
            return (
                tasks
                if tasks and all(task["state"] in {"succeeded", "cancelled"} for task in tasks)
                else None
            )

        tasks = eventually(projection_settled)
        # HTTP requests continue processing the real task; no manual queue drain.
        assert recall(client, TOPICS[0][1])["outcome"] == "empty"
        rows = db_rows(service, mid)
        assert rows == []
        evidence("late-delete", {"memory_id": mid, "rows": rows, "tasks": tasks, "result": "empty"})
