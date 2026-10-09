"""AET-34 / RC-SRC-01,06: real native Query + Azure Milvus, through public HTTP.

Missing prerequisites fail with blocked_fixture; a skip is never acceptance.
All writes and lifecycle changes affect only this test's owned Azure resources.
"""

import json
import os
import time
from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot
from aether_agent_memory.runtime.contracts.models import Permission
from azure_component_service import Service
from component_configuration import ComponentConfiguration
from recall_authorization_support import F1_TEXTS, RecallHTTP, SafeEvidence, assert_denied
from recall_shared_read_support import external_path
from recall_working_search_support import (
    F2_TEXT,
    QUERY,
    WorkingSearchProbe,
    assert_execution,
    assert_source_pack,
)

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p0]


def eventually(check, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Ready fixture barrier did not converge")


def catalog(http, session):
    items, cursor, seen = [], None, set()
    while True:
        response = http.get(
            "/p3/memories",
            params={"session_id": session, "limit": 100, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200
        page = response.json()
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            return items
        assert cursor not in seen
        seen.add(cursor)


def verify_ready(http, ref, text, model_space, kind):
    response = http.get(f"/p3/remember/{ref['memory_id']}")
    assert response.status_code == 200
    memory = MemorySnapshot.model_validate(response.json())
    assert memory.ref.model_dump(mode="json") == ref and memory.ref.version == 1
    assert memory.kind == kind and memory.status == "active"
    assert memory.projection_state == "ready" and memory.model_space == model_space
    assert memory.content == text and memory.content_hash == sha256(text.encode()).hexdigest()
    assert memory.sources
    http.verify_body(ref, text)
    return memory


@pytest.fixture
def working_target(request, tmp_path, monkeypatch):
    options = getattr(request, "param", {})
    case_id = options.get("case_id", "AET-34")
    evidence = SafeEvidence(case_id, request.node.name)
    evidence.data.update(acceptance="failed", lane="native-http-azure")
    directory = (
        external_path(
            Path(
                os.environ.get(
                    "P3_RECALL_EVIDENCE_DIR",
                    str(Path(gettempdir()) / "aether-workspace-support" / case_id),
                )
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(reason):
        evidence.data["acceptance"] = "blocked_fixture"
        evidence.blocked("blocked_fixture", reason)
        pytest.fail("blocked_fixture: " + reason)

    native_path = os.environ.get("P3_TEST_NATIVE_CONFIG")
    if not native_path or not Path(native_path).is_file():
        blocked("P3_TEST_NATIVE_CONFIG with actual local model weights required")
    required = (
        "P3_TEST_STATE_DSN",
        "P3_TEST_REDIS_HOST",
        "P3_TEST_REDIS_PASSWORD",
        "P3_TEST_MILVUS_URI",
        "P3_TEST_MILVUS_TOKEN",
        "P3_TEST_MILVUS_DATABASE",
        "P3_TEST_MILVUS_CA_FILE",
        "P3_TEST_MILVUS_SERVER_NAME",
        "P3_TEST_CEPH_ENDPOINT",
        "P3_TEST_CEPH_BUCKET",
        "P3_TEST_CEPH_ACCESS",
        "P3_TEST_CEPH_SECRET",
    )
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        blocked("real Azure test configuration missing: " + ", ".join(missing))
    try:
        temporal = request.getfixturevalue("temporal_server")
    except RuntimeError:
        blocked("test-owned pinned Temporal CLI unavailable")
    external_path(tmp_path)
    home = {"tenant_id": "t1", "application_id": "app", "user_id": "user", "agent_id": "agent"}
    identity = tmp_path / "identities.yaml"
    identities = []
    for name, permissions in (
        ("U01", [Permission.READ]),
        ("U06", [Permission.READ, Permission.DIAGNOSE]),
        ("fixture-maintainer", list(Permission)),
    ):
        identities.append(
            {
                "credential_sha256": sha256(name.encode()).hexdigest(),
                "principal": {
                    "principal_id": name,
                    "home_scope": home,
                    "permissions": [p.value for p in permissions],
                    "auth_epoch": 1,
                },
            }
        )
    identity.write_text(
        yaml.safe_dump(
            {
                "revision": 1,
                "tenants": [{"tenant_id": "t1"}],
                "identities": identities,
            }
        )
    )
    recall_config = tmp_path / "recall.json"
    recall_config.write_text(
        json.dumps(
            {
                "candidate_limit": options.get("candidate_limit", 20),
                "rerank_policy": "disabled",
            }
        )
    )
    config = ComponentConfiguration(
        data_dir=tmp_path / "state",
        identity_file=identity,
        embedding_profile="native",
        embedding_config=Path(native_path),
        recall_config=recall_config,
        remember={"consolidation_messages": 1},
        periodic_seconds=0.2,
        poll_seconds=0.02,
        shutdown_seconds=1,
        http_wait_seconds=0.05,
        temporal={"deployment_id": "working-search-" + uuid4().hex, "endpoint": temporal.endpoint},
    )
    service = Service(config)
    native = service.runtime.native_embedding
    assert native is not None and service.runtime.embedding is native
    probe = WorkingSearchProbe(service.runtime, monkeypatch)
    evidence.data.update(
        model_space=native.space.model_dump(mode="json"),
        model_binding=native.binding.model_dump(mode="json"),
        backend_binding=service.runtime.vectors.binding(),
        executions=[],
        fixtures=[],
        server_candidate_limit=service.runtime.recall.settings.candidate_limit,
    )
    try:
        with TestClient(service.app()) as client:
            eventually(lambda: client.get("/p3/readyz").status_code == 200, seconds=60)
            yield client, native, probe, evidence
    except BaseException:
        evidence.data["acceptance"] = "failed"
        raise


def recall(
    client,
    native,
    probe,
    evidence,
    actor,
    session,
    source,
    memories,
    label,
    query=QUERY,
    discovery_refs=(),
):
    http = RecallHTTP(client, actor)
    operation_id = label + "-" + uuid4().hex
    payload, job_id = http.command(
        "/p3/recall",
        {
            "query": query,
            "selection": {"session_id": session},
            "sources": source,
            "token_budget": 1000,
        },
        operation_id,
        actor,
    )
    pack = assert_source_pack(payload, source, memories)
    result = http.get(f"/p3/recalls/{pack.recall_id}/result")
    assert result.status_code == 200 and result.json() == payload
    observed = assert_execution(
        probe, operation_id, query, native, source, [m.ref for m in memories], discovery_refs
    )
    observed.update(job_id=job_id, recall_id=pack.recall_id, label=label)
    task = http.get(f"/p3/tasks/{job_id}")
    evidence.response("GET", f"/p3/tasks/{job_id}", task)
    if actor == "U01":
        assert_denied(task, 403, "FORBIDDEN")
    else:
        assert task.status_code == 200
        assert task.json()["task_id"] == job_id and task.json()["initiator_id"] == actor
        trace = http.get(f"/p3/logs/{observed['trace_id']}")
        assert trace.status_code == 200
        evidence.response("GET", f"/p3/logs/{observed['trace_id']}", trace)
    evidence.data["executions"].append(observed)
    return pack


@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("scenario", ["working-ready", "only-long-term-ready"])
def test_working_native_search_never_falls_back(working_target, actor, scenario):
    client, native, probe, evidence = working_target
    maintainer = RecallHTTP(client, "fixture-maintainer")
    # F2 is built by the real Remember pipeline, then its Working input is archived.
    # Its long-term episode stays Ready; no fabricated kind or projection rows.
    session = "f2-" + uuid4().hex
    receipt, _ = maintainer.command(
        "/p3/remember",
        {
            "source": {
                "kind": "text",
                "external_id": session,
                "external_version": "1",
                "occurred_at": "2026-10-09T00:00:00.000Z",
            },
            "selection": {"session_id": session},
            "content": {"kind": "text", "text": F2_TEXT},
        },
        session,
        maintainer.maintainer,
    )
    assert receipt["saved"] and len(receipt["memories"]) == 1

    def ready_long_term():
        return next(
            (
                m
                for m in catalog(maintainer, session)
                if m["kind"] != "working"
                and m["status"] == "active"
                and m["projection_state"] == "ready"
            ),
            None,
        )

    metadata = eventually(ready_long_term)
    f2 = verify_ready(maintainer, metadata["ref"], F2_TEXT, native.model_space, metadata["kind"])
    evidence.data["fixtures"].append(
        {
            "ref": f2.ref.model_dump(mode="json"),
            "body_hash": f2.content_hash,
            "kind": f2.kind,
            "projection_state": f2.projection_state,
        }
    )
    original = receipt["memories"][0]
    response = client.post(
        f"/p3/remember/{original['memory_id']}/lifecycle",
        headers={
            "Authorization": "Bearer fixture-maintainer",
            "X-Operation-ID": "archive-" + session,
        },
        json={
            "expected_version": original["version"],
            "target": "archived",
            "reason": "AET-34 fixture",
        },
    )
    assert response.status_code == 200
    assert not [
        m
        for m in catalog(maintainer, session)
        if m["kind"] == "working" and m["status"] == "active"
    ]
    # Check authority and positive queryability before evaluating Working empty.
    verify_ready(maintainer, metadata["ref"], F2_TEXT, native.model_space, metadata["kind"])
    recall(client, native, probe, evidence, actor, session, "long_term", (f2,), "f2-before")
    if scenario == "working-ready":
        session = "f1-" + uuid4().hex
        proofs = maintainer.prepare_f1(session)
        f1 = tuple(
            verify_ready(maintainer, proof["ref"], text, native.model_space, "working")
            for proof, text in zip(proofs, F1_TEXTS, strict=True)
        )
        evidence.data["fixtures"].extend(
            {
                "ref": m.ref.model_dump(mode="json"),
                "body_hash": m.content_hash,
                "kind": m.kind,
                "projection_state": m.projection_state,
            }
            for m in f1
        )
        recall(
            client,
            native,
            probe,
            evidence,
            actor,
            session,
            "working",
            f1,
            "f1-control",
            query="用户喜欢什么茶？",
        )
        pack = recall(
            client, native, probe, evidence, actor, session, "working", f1, "working-query"
        )
        assert F2_TEXT not in pack.rendered_context
    else:
        # A retained archived vector may be discovered; it must never be delivered.
        recall(
            client,
            native,
            probe,
            evidence,
            actor,
            session,
            "working",
            (),
            "working-empty",
            discovery_refs=(MemoryRef.model_validate(original),),
        )
        recall(client, native, probe, evidence, actor, session, "long_term", (f2,), "f2-after")
    evidence.data["acceptance"] = "passed"
