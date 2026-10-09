"""AET-22 RC-AUTH-01/02: real Azure storage and test-owned Temporal.

This component lane uses controlled embeddings. It is not native-model or
deployment acceptance; missing authoritative observations remain blockers.
"""

import os
import shutil
from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

import httpx
import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient
from pymilvus.exceptions import MilvusException
from redis.exceptions import RedisError

from aether_agent_memory.recall.contracts.models import ContextPack, RecallRecord
from aether_agent_memory.runtime.contracts.models import Permission
from azure_component_service import Service
from component_configuration import ComponentConfiguration
from recall_authorization_support import F1_TEXTS, RecallHTTP, SafeEvidence, assert_denied

pytestmark = [pytest.mark.integration, pytest.mark.p0]

VARIANTS = [
    ("missing", None, 401, "UNAUTHENTICATED"),
    ("unknown", "Bearer unknown", 401, "UNAUTHENTICATED"),
    ("missing-token", "Bearer", 401, "UNAUTHENTICATED"),
    ("empty-token", "Bearer ", 401, "UNAUTHENTICATED"),
    ("wrong-scheme", "Basic dXNlcjpwYXNz", 401, "UNAUTHENTICATED"),
    ("empty-header", "", 401, "UNAUTHENTICATED"),
    ("revoked-credential", "Bearer U09", 401, "UNAUTHENTICATED"),
    ("write-only", "Bearer U05", 403, "FORBIDDEN"),
]


@pytest.fixture
def authorization_target(tmp_path, request):
    variant = request.node.callspec.params["variant"]
    case = "RC-AUTH-02" if variant == "write-only" else "RC-AUTH-01"
    evidence = SafeEvidence(case, variant)
    evidence.data.update(
        lane="real-storage-controlled-model", http_checks="not_run", acceptance="blocked_fixture"
    )
    execution_id = uuid4().hex
    directory = (
        Path(
            os.environ.get(
                "P3_RECALL_EVIDENCE_DIR",
                str(Path(gettempdir()) / "aether-workspace-support/AET-22/executions"),
            )
        )
        / execution_id
    )
    request.addfinalizer(lambda: evidence.write(directory))
    # Each variant gets its own identities, backend namespaces and data copies.
    required = (
        "P3_TEST_STATE_DSN",
        "P3_TEST_REDIS_HOST",
        "P3_TEST_REDIS_PASSWORD",
        "P3_TEST_CEPH_ENDPOINT",
        "P3_TEST_CEPH_BUCKET",
        "P3_TEST_CEPH_ACCESS",
        "P3_TEST_CEPH_SECRET",
        "P3_TEST_MILVUS_URI",
        "P3_TEST_MILVUS_TOKEN",
        "P3_TEST_MILVUS_DATABASE",
        "P3_TEST_MILVUS_CA_FILE",
        "P3_TEST_MILVUS_SERVER_NAME",
    )
    missing = [name for name in required if not os.environ.get(name)]
    temporal_binary = os.environ.get("P3_TEMPORAL_CLI") or shutil.which("temporal")
    if not temporal_binary or not os.access(temporal_binary, os.X_OK):
        missing.append("P3_TEMPORAL_CLI executable")
    for name in missing:
        evidence.blocked("blocked_fixture", name)
    if missing:
        pytest.fail("blocked_fixture: missing " + ", ".join(missing))
    try:
        temporal = request.getfixturevalue("temporal_server")
    except (OSError, RuntimeError, TimeoutError) as error:
        evidence.blocked("blocked_fixture", "Temporal startup: " + type(error).__name__)
        raise
    scope = {
        "tenant_id": "T-" + execution_id,
        "application_id": "App-A",
        "user_id": "User-A",
        "agent_id": "Agent-A",
    }
    principals = {
        "U01": [Permission.READ],
        "U05": [Permission.WRITE],
        "U09": [Permission.READ],
        "maintainer": [Permission.READ, Permission.WRITE, Permission.DIAGNOSE],
    }
    identities = {
        "revision": 1,
        "tenants": [{"tenant_id": scope["tenant_id"]}],
        "identities": [
            {
                "credential_sha256": sha256(name.encode()).hexdigest(),
                "principal": {
                    "principal_id": name,
                    "auth_epoch": 1,
                    "home_scope": scope,
                    "permissions": [p.value for p in permissions],
                },
            }
            for name, permissions in principals.items()
        ],
    }
    identity_file = tmp_path / "identities.yaml"
    identity_file.write_text(yaml.safe_dump(identities), encoding="utf-8")
    configuration = ComponentConfiguration(
        data_dir=tmp_path / "state",
        identity_file=identity_file,
        embedding_profile="injected",
        remember={"consolidation_messages": 1000},
        maintenance_principals=("maintainer",),
        periodic_seconds=0.2,
        poll_seconds=0.02,
        shutdown_seconds=1,
        http_wait_seconds=0.05,
        temporal={"deployment_id": "auth-" + execution_id, "endpoint": temporal.endpoint},
    )
    try:
        service = Service(configuration)
    except (
        psycopg.OperationalError,
        RedisError,
        MilvusException,
        httpx.TransportError,
        FileNotFoundError,
    ) as error:
        evidence.blocked(
            "blocked_fixture",
            "backend bootstrap: " + type(error).__module__ + "." + type(error).__name__,
        )
        raise
    evidence.data["lane"] = "real-storage-controlled-model"
    evidence.data["permission_contract"] = {
        "READ": Permission.READ.value,
        "WRITE": Permission.WRITE.value,
    }
    # These gaps cannot be healed by granting DIAGNOSE or inventing policy.
    evidence.blocked("blocked_fixture", "native model and deployed source SHA/image digest binding")
    evidence.blocked("blocked_fixture", "published projection generation via authorized HTTP view")
    evidence.blocked("blocked_fixture", "authorization decision and model/body-call evidence view")
    if variant == "revoked-credential":
        evidence.blocked(
            "blocked_requirement", "Q01: JWT expiration policy (credential removal only)"
        )
    with TestClient(service.app()) as client:
        harness = RecallHTTP(client, "maintainer")
        evidence.data["identities"] = {
            name: {"scope": scope, "permissions": [p.value for p in permissions]}
            for name, permissions in principals.items()
        }
        yield harness, service, identities, evidence, execution_id


@pytest.mark.parametrize(
    "variant,authorization,status,code", VARIANTS, ids=[v[0] for v in VARIANTS]
)
def test_denied_identity_cannot_recall_or_retrieve_original_pack(
    variant, authorization, status, code, authorization_target
):
    harness, service, identities, evidence, execution_id = authorization_target
    session = "S-" + execution_id
    proofs = harness.prepare_f1(session)
    evidence.data["F1"] = proofs
    request = {
        "query": "用户的咖啡加糖习惯是什么？",
        "selection": {"session_id": session},
        "sources": "working",
        "token_budget": 1000,
    }
    original_token = "U09" if variant == "revoked-credential" else "U01"
    result, job_id = harness.command("/p3/recall", request, "read-control", original_token)
    pack = ContextPack.model_validate(result)
    assert F1_TEXTS[0] in pack.rendered_context, "READ control must actually recall coffee"
    delivered = [item.memory.model_dump(mode="json") for g in pack.groups for item in g.items]
    assert proofs[0]["ref"] in delivered, "READ control must bind the exact coffee Ref"
    record_response = harness.get(f"/p3/recalls/{pack.recall_id}", original_token)
    assert record_response.status_code == 200
    record = RecallRecord.model_validate(record_response.json())
    assert record.state == "completed" and record.result_available
    evidence.data["control"] = {
        "operation_id": "read-control",
        "job_id": job_id,
        "recall_id": pack.recall_id,
        "record": record.model_dump(mode="json"),
        "pack_hash": sha256(pack.model_dump_json().encode()).hexdigest(),
    }
    # A separate legal tea query proves both F1 memories are usable, not just Ready.
    tea, _ = harness.command(
        "/p3/recall", {**request, "query": "用户喜欢什么茶？"}, "tea-control", "U01"
    )
    assert F1_TEXTS[1] in ContextPack.model_validate(tea).rendered_context
    if variant == "revoked-credential":
        identities["revision"] += 1
        identities["identities"] = [
            entry
            for entry in identities["identities"]
            if entry["principal"]["principal_id"] != "U09"
        ]
        service.config.identity_file.write_text(yaml.safe_dump(identities), encoding="utf-8")
        service.reload_identity()  # Existing owner control-plane preparation, no auth patch.
        assert harness.get("/p3/auth/me", "U09").status_code == 401
        evidence.data["invalidation"] = {"mechanism": "credential_removal", "revision": 2}
    before = harness.recall_tasks()
    headers = {"X-Operation-ID": "denied-recall"}
    if authorization is not None:
        headers["Authorization"] = authorization
    response = harness.client.post("/p3/recall", json=request, headers=headers)
    assert_denied(response, status, code)
    evidence.response("POST", "/p3/recall", response)
    after = harness.recall_tasks()
    new = after.keys() - before.keys()
    observed = []
    for denied_job in new:
        terminal = harness.poll_job(denied_job)
        assert terminal["state"] != "succeeded", "denial persisted a successful job"
        assert terminal["error_code"] == code, "denial terminal reason differs"
        observed.append({"job_id": denied_job, "state": terminal["state"], "code": code})
        denied_result = harness.get(f"/p3/operations/{denied_job}/result")
        assert_denied(denied_result, status, code)
    evidence.data["denied_jobs"] = observed
    evidence.data["admission"] = "terminal_denial" if new else "not_admitted"
    for path in (
        f"/p3/recalls/{pack.recall_id}",
        f"/p3/recalls/{pack.recall_id}/result",
        f"/p3/operations/{job_id}",
        f"/p3/operations/{job_id}/result",
    ):
        response = harness.client.get(path, headers=headers)
        assert_denied(response, status, code)
        evidence.response("GET", path, response)
    if variant == "write-only":
        me = harness.get("/p3/auth/me", "U05")
        assert me.status_code == 200
        assert me.json()["permissions"] == [Permission.WRITE.value]
        reader = harness.get("/p3/auth/me", "U01")
        assert reader.status_code == 200
        assert me.json()["scope"] == reader.json()["scope"], "U05 must share the U01 scope"
        diagnostic = harness.get("/p3/tasks", "U05")
        assert_denied(diagnostic, 403, "FORBIDDEN")
    if variant != "revoked-credential":
        original = harness.get(f"/p3/recalls/{pack.recall_id}/result", "U01")
        assert original.status_code == 200 and original.json() == result
    evidence.data["http_checks"] = "passed"
    evidence.data["acceptance"] = "blocked_fixture"
    pytest.xfail(
        "blocked_fixture: HTTP checks complete; native/projection/decision evidence missing"
    )
