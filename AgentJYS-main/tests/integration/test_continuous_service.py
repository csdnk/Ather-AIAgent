"""Use the public HTTP boundary and real durable workers; no manual drain."""

import asyncio
import hashlib
import os
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.runtime.contracts.models import Permission
from aether_agent_memory.runtime.flows.application import Service
from aether_agent_memory.runtime.flows.config import ServiceConfiguration


def provision(path, *, revision=1, enabled=True, grants=(), epoch=1):
    entries = []
    for user, tenant in [("alice", "t1"), ("bob", "t1"), ("eve", "t2")]:
        entries.append(
            {
                "credential_sha256": hashlib.sha256(user.encode()).hexdigest(),
                "principal": {
                    "principal_id": user,
                    "home_scope": {
                        "tenant_id": tenant,
                        "application_id": "app",
                        "user_id": user,
                        "agent_id": user,
                    },
                    "permissions": [p.value for p in Permission],
                    "auth_epoch": epoch,
                },
            }
        )
    path.write_text(
        yaml.safe_dump(
            {
                "revision": revision,
                "tenants": [
                    {"tenant_id": "t1", "enabled": enabled},
                    {"tenant_id": "t2", "enabled": True},
                ],
                "identities": entries,
                "grants": list(grants),
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def configuration(tmp_path, temporal_server):
    auth = tmp_path / "identities.yaml"
    provision(auth)
    return ServiceConfiguration(
        temporal={
            "deployment_id": hashlib.sha256(str(tmp_path).encode()).hexdigest()[:32],
            "endpoint": temporal_server.endpoint,
        },
        data_dir=tmp_path / "state",
        identity_file=auth,
        embedding_profile="lexical",
        poll_seconds=0.01,
        periodic_seconds=0.02,
        identity_reload_seconds=0.02,
        shutdown_seconds=1,
        remember=RememberPolicy(consolidation_messages=1),
        operate_decay_seconds=0.1,
        operate_audit_seconds=0.1,
    )


def headers(user="alice", operation=None):
    result = {"Authorization": "Bearer " + user}
    if operation:
        result["X-Operation-ID"] = operation
    return result


def save(client, text="I prefer unsweetened coffee.", operation="save_1"):
    return client.post(
        "/p3/remember",
        headers=headers(operation=operation),
        json={
            "source": {
                "kind": "conversation",
                "external_id": operation,
                "external_version": "1",
                "occurred_at": "2026-09-26T00:00:00.000Z",
            },
            "selection": {"session_id": "s1"},
            "content": {"kind": "text", "text": text},
        },
    )


def eventually(check, seconds=60):
    until = time.monotonic() + seconds
    result = None
    while time.monotonic() < until:
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError(f"continuous processing did not converge: {result}")


def ready_memory(client):
    response = client.get("/p3/memories", headers=headers())
    assert response.status_code == 200, response.text
    return next(
        (
            m
            for m in response.json()["items"]
            if m["kind"] != "working" and m["projection_state"] == "ready"
        ),
        None,
    )


def test_http_pipeline_is_continuous_durable_and_idempotent(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        accepted = save(client)
        assert accepted.status_code == 200, accepted.text
        first = accepted.json()
        assert save(client).json() == first
        memory = eventually(lambda: ready_memory(client))
        result = client.post(
            "/p3/recall",
            headers=headers(operation="recall_1"),
            json={
                "query": "unsweetened coffee",
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
        )
        assert result.status_code == 200, result.text
        assert "unsweetened coffee" in result.json()["rendered_context"]
        assert eventually(
            lambda: client.get("/p3/runtime", headers=headers()).json()["http_worker"] == "running"
        )
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.rows("operate_views")
        memory_id = memory["ref"]["memory_id"]
        recall_id = result.json()["recall_id"]
    with TestClient(Service(configuration).app()) as restarted:
        response = restarted.get(f"/p3/recalls/{recall_id}/result", headers=headers())
        assert response.status_code == 200, response.text
        assert "unsweetened coffee" in response.json()["rendered_context"]
        assert restarted.get(f"/p3/remember/{memory_id}", headers=headers("eve")).status_code == 403


def test_tenant_disable_and_stale_config_do_not_resurrect(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        assert save(client).status_code == 200
        eventually(lambda: ready_memory(client))
        provision(configuration.identity_file, revision=2, enabled=False)
        eventually(lambda: client.get("/p3/memories", headers=headers()).status_code == 403)
        provision(configuration.identity_file, revision=1, enabled=True)
        eventually(lambda: service.supervisor.state["identity"] == "degraded")
        assert client.get("/p3/memories", headers=headers()).status_code == 403
        assert client.get("/p3/memories", headers=headers("eve")).status_code == 200


@pytest.mark.skipif(
    not os.environ.get("P3_TEST_NATIVE_CONFIG"), reason="explicit native model config required"
)
@pytest.mark.parametrize("sources", ["long_term", "working"])
def test_native_embedding_runs_through_unified_http(configuration, sources):
    configuration = configuration.model_copy(
        update={
            "embedding_profile": "native",
            "embedding_config": Path(os.environ["P3_TEST_NATIVE_CONFIG"]),
        }
    )
    service = Service(configuration)
    with TestClient(service.app()) as client:
        assert service.runtime.native_embedding.space.dimensions == 512
        assert save(client, "用户喜欢喝不加糖的咖啡。", "native_save").status_code == 200
        eventually(lambda: ready_memory(client), seconds=60)
        result = client.post(
            "/p3/recall",
            headers=headers(),
            json={
                "query": "用户喝咖啡有什么偏好？",
                "selection": {"session_id": "s1"} if sources == "working" else {},
                "sources": sources,
                "token_budget": 1000,
            },
        )
        assert result.status_code == 200, result.text
        assert "不加糖" in result.json()["rendered_context"]
        recall_id = result.json()["recall_id"]
    with TestClient(Service(configuration).app()) as client:
        assert (
            "不加糖"
            in client.get(f"/p3/recalls/{recall_id}/result", headers=headers()).json()[
                "rendered_context"
            ]
        )


def test_document_ingress_persists_original_and_enforces_scope(configuration):
    with TestClient(Service(configuration).app()) as client:
        data = "会议纪要。退款时限为三十天。\n".encode()
        response = client.put(
            "/p3/documents/meeting?version=1",
            headers={**headers(), "Content-Type": "text/plain"},
            content=data,
        )
        assert response.status_code == 200, response.text
        document = response.json()
        assert document["expected_hash"] == hashlib.sha256(data).hexdigest()
        conflict = client.put(
            "/p3/documents/meeting?version=1",
            headers={**headers(), "Content-Type": "text/plain"},
            content=b"changed",
        )
        assert conflict.status_code == 409
        body = {
            "source": {
                "kind": "document",
                "external_id": "meeting",
                "external_version": "1",
                "occurred_at": "2026-09-26T00:00:00.000Z",
            },
            "selection": {"session_id": "s1"},
            "content": document,
        }
        rejected = client.post("/p3/remember", headers=headers("eve"), json=body)
        assert rejected.status_code in {403, 404}, rejected.text
        saved = client.post("/p3/remember", headers=headers(), json=body)
        assert saved.status_code == 200, saved.text


def test_shared_recall_and_revocation_use_current_resource_permission(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        assert save(client).status_code == 200
        memory = eventually(lambda: ready_memory(client))
        payload = {
            "query": "unsweetened coffee",
            "selection": {},
            "sources": "long_term",
            "token_budget": 1000,
        }
        assert (
            client.post("/p3/recall", headers=headers("bob"), json=payload).json()["outcome"]
            == "empty"
        )
        grant = {
            "grant_id": "share_coffee",
            "grantee_id": "bob",
            "grantee_tenant_id": "t1",
            "revision": 1,
            "permissions": ["memory:read"],
            "resource": {
                "owner": "remember",
                "object_type": "memory",
                "object_id": memory["ref"]["memory_id"],
                "scope": memory["ref"]["scope"],
            },
        }
        provision(configuration.identity_file, revision=2, grants=[grant])
        eventually(lambda: service.identity_hash == configuration.identity_file.read_bytes())
        response = client.post("/p3/recall", headers=headers("bob"), json=payload)
        assert response.status_code == 200, response.text
        assert "unsweetened coffee" in response.json()["rendered_context"]
        recall_id = response.json()["recall_id"]
        provision(configuration.identity_file, revision=3)
        eventually(lambda: service.identity_hash == configuration.identity_file.read_bytes())
        assert client.get(
            f"/p3/recalls/{recall_id}/result", headers=headers("bob")
        ).status_code in {403, 410}
        assert (
            client.post("/p3/recall", headers=headers("bob"), json=payload).json()["outcome"]
            == "empty"
        )


def test_operate_uses_prepared_files_and_cools_without_new_requests(configuration, monkeypatch):
    import aether_agent_memory.operate.basic.continuous as continuous

    # Exercise the domain's decay window independently of Temporal/CI latency.
    # Only the heat-policy time scale changes; identity, deadlines and Workers
    # retain their real clocks. The silent cooling jump sends no new request.
    parse_seconds, origin, advance = continuous.seconds, time.time(), [0.0]
    monkeypatch.setattr(
        continuous,
        "seconds",
        lambda timestamp: origin + (parse_seconds(timestamp) - origin) * 0.001 + advance[0],
    )
    configuration = configuration.model_copy(
        update={"operate_decay_seconds": 4.0, "operate_retry_seconds": 0.1}
    )
    service = Service(configuration)
    with TestClient(service.app()) as client:
        assert save(client).status_code == 200
        memory = eventually(lambda: ready_memory(client))
        memory_id = memory["ref"]["memory_id"]
        path = f"/p3/operate/memories/{memory_id}"
        for i in range(16):
            response = client.post(
                "/p3/recall",
                headers=headers(operation=f"heat_{i}"),
                json={
                    "query": "unsweetened coffee",
                    "selection": {},
                    "sources": "long_term",
                    "token_budget": 1000,
                },
            )
            assert response.status_code == 200, response.text

        def promoted():
            response = client.get(path, headers=headers())
            assert response.status_code == 200, response.text
            return any(
                a["state"] == "succeeded" and a["intent"]["decision"]["target_tier"] == "hot"
                for a in response.json()["actions"]
            )

        eventually(promoted)
        response = client.post(
            "/p3/recall",
            headers=headers(),
            json={
                "query": "unsweetened coffee",
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
        )
        assert response.status_code == 200, response.text
        with service.runtime.executor.db() as db:
            assert db.execute("SELECT sum(count) FROM reads").fetchone()[0] > 0

        # Recall commits its access Outbox before Operate consumes it. Let all
        # 17 observations reach the heat policy before advancing its clock.
        def all_accesses_consumed():
            with service.runtime.foundation.uow.transaction() as tx:
                return any(
                    row["memory"]["key"]["memory_id"] == memory_id
                    and row["access_count"] == 17
                    for _, row in tx.rows("operate_heat")
                )

        eventually(all_accesses_consumed)
        advance[0] = 40.0

        def cooled():
            return any(
                a["state"] == "succeeded" and a["intent"]["decision"]["target_tier"] == "cold"
                for a in client.get(path, headers=headers()).json()["actions"]
            )

        eventually(cooled, seconds=60)


def test_correction_and_delete_invalidate_previous_context(configuration):
    with TestClient(Service(configuration).app()) as client:
        assert save(client).status_code == 200
        memory = eventually(lambda: ready_memory(client))
        mid = memory["ref"]["memory_id"]
        old = client.post(
            "/p3/recall",
            headers=headers(),
            json={
                "query": "unsweetened coffee",
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
        ).json()
        corrected = client.post(
            f"/p3/remember/{mid}/correct",
            headers=headers(),
            json={
                "expected_version": 1,
                "content": "I prefer green tea now.",
                "reason": "user correction",
                "source": {
                    "kind": "conversation",
                    "external_id": "correction",
                    "external_version": "1",
                    "occurred_at": "2026-09-26T01:00:00.000Z",
                },
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert (
            client.get(f"/p3/recalls/{old['recall_id']}/result", headers=headers()).status_code
            == 410
        )

        def revised():
            item = client.get(f"/p3/remember/{mid}", headers=headers()).json()
            return (
                item
                if item["ref"]["version"] == 2 and item["projection_state"] == "ready"
                else None
            )

        current = eventually(revised)
        deleted = client.post(
            f"/p3/remember/{mid}/delete",
            headers=headers(),
            json={
                "expected_revision": current["object_revision"],
                "reason": "user deletion",
            },
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["blocked"]
        result = client.post(
            "/p3/recall",
            headers=headers(),
            json={
                "query": "green tea",
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
        )
        assert result.status_code == 200, result.text
        assert "green tea" not in result.json()["rendered_context"]


def test_readiness_detects_object_store_outage(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/ready", headers=headers()).status_code == 200)
        service.runtime.remember.bodies.p2.available = False
        assert client.get("/p3/ready", headers=headers()).status_code == 503
        assert client.get("/p3/live").status_code == 200
        service.runtime.remember.bodies.p2.available = True
        assert client.get("/p3/ready", headers=headers()).status_code == 200


def test_cold_cache_is_reclaimed_and_next_access_wakes_it(configuration):
    configuration = configuration.model_copy(
        update={
            "operate_stats_retention_seconds": 0.2,
            "operate_retry_seconds": 0.05,
            "operate_decay_seconds": 30,
        }
    )
    service = Service(configuration)
    with TestClient(service.app()) as client:
        assert save(client).status_code == 200
        memory = eventually(lambda: ready_memory(client))
        mid = memory["ref"]["memory_id"]
        path = f"/p3/operate/memories/{mid}"
        # This verifies eventual reclamation, not an 8-second latency SLO.
        eventually(
            lambda: client.get(path, headers=headers()).json()["input"].get("dormant"), seconds=60
        )
        with service.runtime.executor.db() as db:
            assert not any(mid in r[0] for r in db.execute("SELECT memory FROM copies"))
        result = client.post(
            "/p3/recall",
            headers=headers(),
            json={
                "query": "coffee",
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
        )
        assert "coffee" in result.json()["rendered_context"]
        eventually(
            lambda: any(
                a["state"] == "succeeded"
                for a in client.get(path, headers=headers()).json()["actions"]
            ),
            seconds=60,
        )


def test_slow_extraction_does_not_block_http_or_operate(configuration):
    import threading

    from aether_agent_memory.remember.basic.extraction import LiteralExtraction

    started = threading.Event()
    release = threading.Event()

    class SlowExtraction(LiteralExtraction):
        async def extract(self, ctx, request):
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return await super().extract(ctx, request)

    service = Service(configuration, extraction=SlowExtraction())
    with TestClient(service.app()) as client:
        try:
            assert save(client).status_code == 200
            eventually(started.is_set)
            assert client.get("/p3/live").status_code == 200

            def operated():
                with service.runtime.foundation.uow.transaction() as tx:
                    return any(
                        r["record"]["kind"] == "operate.evaluate"
                        and r["record"]["state"] == "succeeded"
                        for _, r in tx.rows("tasks")
                    )

            eventually(operated)
        finally:
            release.set()
        eventually(lambda: ready_memory(client))


def test_interrupted_extraction_is_recovered_after_restart(configuration):
    import threading

    from aether_agent_memory.remember.basic.extraction import LiteralExtraction

    started = threading.Event()

    class Interrupted(LiteralExtraction):
        async def extract(self, ctx, request):
            started.set()
            await asyncio.Event().wait()

    configuration = configuration.model_copy(update={"shutdown_seconds": 0.1})
    service = Service(configuration, extraction=Interrupted())
    service.runtime.foundation.tasks.lease_seconds = 0.2
    with TestClient(service.app()) as client:
        assert save(client).status_code == 200
        eventually(started.is_set)
    with TestClient(Service(configuration).app()) as client:
        eventually(lambda: ready_memory(client))


def test_unknown_action_queries_original_id_after_restart(configuration):
    configuration = configuration.model_copy(update={"operate_decay_seconds": 3600})
    service = Service(configuration)

    async def unavailable(ctx, action_id):
        raise ConnectionError("query temporarily unavailable")

    with TestClient(service.app()) as client:
        assert save(client).status_code == 200
        memory = eventually(lambda: ready_memory(client))
        mid = memory["ref"]["memory_id"]
        service.runtime.executor.drop_next_response = True
        service.runtime.executor.query = unavailable
        response = client.post(
            "/p3/recall",
            headers=headers(),
            json={
                "query": "coffee",
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
        )
        assert response.status_code == 200, response.text
        path = f"/p3/operate/memories/{mid}"

        def unknown():
            return next(
                (
                    a
                    for a in client.get(path, headers=headers()).json()["actions"]
                    if a["state"] == "unknown"
                ),
                None,
            )

        action_id = eventually(unknown)["intent"]["action_id"]
    with TestClient(Service(configuration).app()) as client:

        def recovered():
            return any(
                a["state"] == "succeeded" and a["intent"]["action_id"] == action_id
                for a in client.get(path, headers=headers()).json()["actions"]
            )

        # A killed/stopping worker's delivery can remain live until Temporal's
        # 10-second heartbeat timeout; the old 8-second wait ended too early.
        eventually(recovered, seconds=60)
        actions = client.get(path, headers=headers()).json()["actions"]
        assert len(actions) == 1


def test_capacity_defers_without_unknown_and_recovers(configuration):
    configuration = configuration.model_copy(update={"operate_retry_seconds": 0.05})
    service = Service(configuration)
    service.runtime.executor.capacity = 1
    with TestClient(service.app()) as client:
        assert save(client).status_code == 200
        memory = eventually(lambda: ready_memory(client))
        mid = memory["ref"]["memory_id"]

        def deferred():
            with service.runtime.foundation.uow.transaction() as tx:
                return any(
                    r["value"] == {"deferred": "cache_capacity"} for _, r in tx.rows("records")
                )

        eventually(deferred)
        assert not client.get(f"/p3/operate/memories/{mid}", headers=headers()).json()["actions"]
        service.runtime.executor.capacity = 4096

        def prepared():
            with service.runtime.executor.db() as db:
                return any(mid in row[0] for row in db.execute("SELECT memory FROM copies"))

        eventually(prepared)


def test_tenant_reactivation_requires_new_epoch(configuration):
    from aether_agent_memory.runtime.foundation.common import FoundationError

    service = Service(configuration)
    with TestClient(service.app()) as client:
        old = service.runtime.foundation.identity.context("alice")
        provision(configuration.identity_file, revision=2, enabled=False)
        eventually(lambda: client.get("/p3/memories", headers=headers()).status_code == 403)
        provision(configuration.identity_file, revision=3)
        eventually(lambda: service.supervisor.state["identity"] == "degraded")
        assert client.get("/p3/memories", headers=headers()).status_code == 403
        provision(configuration.identity_file, revision=4, epoch=2)
        eventually(lambda: client.get("/p3/memories", headers=headers()).status_code == 200)
        with pytest.raises(FoundationError), service.runtime.foundation.uow.transaction() as tx:
            service.runtime.foundation.identity.revalidate(tx, old)


def test_json_model_extraction_is_consumed_by_durable_pipeline(configuration):
    import json

    import httpx

    from aether_agent_memory.remember.basic.extraction import LangMemBatchExtraction
    from aether_agent_memory.remember.model_provider import ModelProvider
    from aether_agent_memory.runtime.flows.config import LanguageModel

    def respond(request):
        body = json.loads(json.loads(request.content)["messages"][-1]["content"])
        source = body["sources"][0]
        value = {
            "facts": [
                {
                    "text": source["text"],
                    "kind": "semantic",
                    "fact_key": "preference",
                    "importance_category": "fact",
                    "evidence": [{"source_id": source["source_id"], "quote": source["text"]}],
                }
            ]
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]
            },
        )

    model = ModelProvider(
        LanguageModel(model="protocol_fixture"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    service = Service(configuration, extraction=LangMemBatchExtraction(model, "protocol_fixture"))
    with TestClient(service.app()) as client:
        try:
            assert save(client).status_code == 200
            memory = eventually(lambda: ready_memory(client))
            assert memory["kind"] == "semantic"
            response = client.post(
                "/p3/recall",
                headers=headers(),
                json={
                    "query": "coffee",
                    "selection": {},
                    "sources": "long_term",
                    "token_budget": 1000,
                },
            )
            assert "unsweetened coffee" in response.json()["rendered_context"]
        finally:
            client.portal.call(model.close)


def test_reactivation_during_old_cleanup_keeps_scheduling_alive(configuration):
    import threading

    service = Service(configuration)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = service.runtime.operate.submit_evaluation

    async def held(ctx, task, prepared):
        with service.runtime.foundation.uow.transaction() as tx:
            cleanup = tx.get(task.input_ref)["cleanup"]
        if cleanup:
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        result = await original(ctx, task, prepared)
        if cleanup:
            finished.set()
        return result

    with TestClient(service.app()) as client:
        assert save(client).status_code == 200
        memory = eventually(lambda: ready_memory(client))
        mid = memory["ref"]["memory_id"]
        path = f"/p3/operate/memories/{mid}"
        service.runtime.operate.submit_evaluation = held
        try:
            for target in ("archived", "active"):
                response = client.post(
                    f"/p3/remember/{mid}/lifecycle",
                    headers=headers(),
                    json={
                        "expected_version": 1,
                        "target": target,
                        "reason": "lifecycle race test",
                    },
                )
                assert response.status_code == 200, response.text
                if target == "archived":
                    eventually(started.is_set)
            eventually(
                lambda: client.get(path, headers=headers()).json()["input"].get("cleanup") is False
            )
        finally:
            release.set()
        eventually(finished.is_set)
        state = client.get(path, headers=headers()).json()["input"]
        assert state["cleanup"] is False and state["cleanup_completed"] is False
