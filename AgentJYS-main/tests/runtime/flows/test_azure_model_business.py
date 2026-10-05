"""Real model/storage gate with explicit real Ceph or controlled-object selection.

Synthetic identities and test-owned Temporal remain separately identified.
"""

import base64
import hashlib
import io
import json
import os
import threading
from pathlib import Path

import pytest
import yaml
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from fastapi.testclient import TestClient
from test_azure_storage_assembly import configured
from test_postgres_observability import dsns as dsns
from tests.integration.test_current_p2_http import command, eventually, headers

from aether_agent_memory.runtime.contracts.models import Permission
from aether_agent_memory.runtime.flows.application import Service
from aether_agent_memory.runtime.flows.config import LanguageModel
from aether_agent_memory.runtime.storage.configuration import CephStorageConfiguration
from azure_storage_support import azure_redis as azure_redis

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("P3_REQUIRE_REAL_MODEL") != "1",
        reason="explicit real model business acceptance required",
    ),
]


@pytest.fixture
def controlled_objects(monkeypatch):
    original = BaseClient._make_api_call
    values, lock = {}, threading.Lock()

    def call(client, operation, params):
        if client.meta.service_model.service_name != "s3":
            return original(client, operation, params)
        address = (params["Bucket"], params["Key"])
        with lock:
            if operation == "PutObject":
                body = params["Body"]
                assert params["IfNoneMatch"] == "*"
                assert params["ContentLength"] == len(body)
                assert params["Metadata"] == {"sha256": hashlib.sha256(body).hexdigest()}
                assert (
                    params["ContentMD5"]
                    == base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()
                )
                if address in values:
                    raise ClientError(
                        {
                            "Error": {"Code": "PreconditionFailed"},
                            "ResponseMetadata": {"HTTPStatusCode": 412},
                        },
                        operation,
                    )
                values[address] = body
                return {}
            if operation == "DeleteObject":
                values.pop(address, None)
                return {}
            if operation not in {"GetObject", "HeadObject"}:
                raise AssertionError("unexpected controlled S3 operation: " + operation)
            if address not in values:
                raise ClientError(
                    {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                    operation,
                )
            full = body = values[address]
            response = {
                "Metadata": {"sha256": hashlib.sha256(full).hexdigest()},
                "ResponseMetadata": {"HTTPStatusCode": 200},
            }
            if "Range" in params:
                start, last = map(int, params["Range"].removeprefix("bytes=").split("-"))
                body = full[start : last + 1]
                response.update(
                    ContentRange=f"bytes {start}-{last}/{len(full)}",
                    ResponseMetadata={"HTTPStatusCode": 206},
                )
            response["ContentLength"] = len(body)
            if operation == "GetObject":
                response["Body"] = StreamingBody(io.BytesIO(body), len(body))
            return response

    monkeypatch.setattr(BaseClient, "_make_api_call", call)
    return values


@pytest.fixture
def object_backend(request):
    if os.environ.get("P3_REQUIRE_CEPH") == "1":
        return None
    return request.getfixturevalue("controlled_objects")


def test_real_model_storage_flow_retains_results_across_service_reconstruction(
    tmp_path, dsns, azure_redis, temporal_server, object_backend, monkeypatch
):
    config = configured(tmp_path, dsns, azure_redis, monkeypatch)
    real_ceph = os.environ.get("P3_REQUIRE_CEPH") == "1"
    if real_ceph:
        ceph = CephStorageConfiguration(
            endpoint=os.environ["P3_TEST_CEPH_ENDPOINT"],
            bucket=os.environ["P3_TEST_CEPH_BUCKET"],
            access_key_env="P3_TEST_CEPH_ACCESS",
            secret_key_env="P3_TEST_CEPH_SECRET",
            allow_insecure_http=os.environ.get("P3_TEST_CEPH_ALLOW_HTTP") == "1",
            ca_file=os.environ.get("P3_TEST_CEPH_CA_FILE"),
        )
        config = config.model_copy(
            update={"azure_storage": config.azure_storage.model_copy(update={"ceph": ceph})}
        )
    native = Path(os.environ["P3_TEST_NATIVE_CONFIG"])
    assert native.is_file(), "real BGE configuration must exist"
    config = config.model_copy(
        update={
            "language_model": LanguageModel(
                endpoint=os.environ["P3_TEST_LLM_ENDPOINT"],
                model=os.environ["P3_TEST_LLM_MODEL"],
                api_key_env="P3_TEST_LLM_KEY",
                response_format="json_schema",
                timeout_seconds=90,
            ),
            "embedding_config": native,
            "remember": config.remember.model_copy(update={"consolidation_messages": 1}),
            "temporal": config.temporal.model_copy(
                update={
                    "endpoint": temporal_server.endpoint,
                    "deployment_id": config.azure_storage.namespace,
                }
            ),
            "periodic_seconds": 1,
            "poll_seconds": 0.2,
            "http_wait_seconds": 0.1,
            "shutdown_seconds": 3,
        }
    )
    config.identity_file.write_text(
        yaml.safe_dump(
            {
                "revision": 1,
                "tenants": [{"tenant_id": "t1"}, {"tenant_id": "t2"}],
                "identities": [
                    {
                        "credential_sha256": hashlib.sha256(user.encode()).hexdigest(),
                        "principal": {
                            "principal_id": user,
                            "auth_epoch": 1,
                            "permissions": [p.value for p in Permission],
                            "home_scope": {
                                "tenant_id": tenant,
                                "application_id": "app",
                                "user_id": user,
                                "agent_id": "agent",
                            },
                        },
                    }
                    for user, tenant in (("alice", "t1"), ("eve", "t2"))
                ],
            }
        ),
        encoding="utf8",
    )
    text = "陈林偏好无糖红茶，阅读时需要安静的座位。"
    source = {
        "kind": "conversation",
        "external_id": "azure-model-source",
        "external_version": "1",
        "occurred_at": "2026-10-03T00:00:00.000Z",
    }
    request = {
        "source": source,
        "selection": {"task_id": "model-business-task", "session_id": "original-session"},
        "content": {"kind": "text", "text": text},
    }
    query = {
        "query": "陈林喝茶需要放蔗糖吗？",
        # Selection filters stored resources; it does not identify the caller's
        # current conversation. Cross-session Recall keeps the shared task.
        "selection": {"task_id": "model-business-task"},
        "sources": "long_term",
        "token_budget": 1000,
    }
    service = Service(config)
    vectors = service.runtime.vectors
    assert service.runtime.foundation.uow.backend == "postgresql"
    assert service.runtime.native_embedding.space.dimensions == 512
    assert service.runtime.executor.provider_id == "redis_hot_cache"
    try:
        with TestClient(service.app()) as client:
            eventually(lambda: client.get("/p3/readyz").status_code == 200)
            saved, location = command(client, "/p3/remember", request, "model-save")
            repeated, repeated_location = command(client, "/p3/remember", request, "model-save")
            assert repeated == saved and repeated_location == location

            def extracted():
                response = client.get("/p3/memories", headers=headers())
                assert response.status_code == 200, response.text
                return [
                    item
                    for item in response.json()["items"]
                    if item["kind"] == "semantic" and item["projection_state"] == "ready"
                ]

            memories = eventually(extracted)
            restricted, _ = command(
                client,
                "/p3/recall",
                {
                    **query,
                    "selection": {"task_id": "model-business-task", "session_id": "new-session"},
                },
                "model-recall-new-session-only",
            )
            assert restricted["rendered_context"] == ""
            first, recall_location = command(client, "/p3/recall", query, "model-recall")
            assert "无糖红茶" in first["rendered_context"]
            assert (
                client.get(
                    f"/p3/remember/{memories[0]['ref']['memory_id']}", headers=headers(user="eve")
                ).status_code
                == 403
            )
            if not real_ceph:
                assert object_backend
            assert not list(config.data_dir.rglob("*.db"))
            assert vectors.client.has_collection(vectors.collection, timeout=10)

        rebuilt = Service(config)
        vectors = rebuilt.runtime.vectors
        with TestClient(rebuilt.app()) as client:
            eventually(lambda: client.get("/p3/readyz").status_code == 200)
            assert client.get(location + "/result", headers=headers()).json() == saved
            assert client.get(recall_location + "/result", headers=headers()).json() == first
            again, _ = command(client, "/p3/recall", query, "model-recall-after-restart")
            assert "无糖红茶" in again["rendered_context"]
            # No original conversation or saved body was included in either Recall request.
            assert set(query) == {"query", "selection", "sources", "token_budget"}
            evidence = {
                "model": config.language_model.model,
                "run_storage": "Azure PostgreSQL/Redis/Milvus",
                "embedding": "real BGE 512",
                "objects": "real Ceph RGW" if real_ceph else "controlled S3 responses",
                "temporal": "test-owned local server",
                "identity": "synthetic static identities",
                "saved_job": location,
                "recall_job": recall_location,
                "saved": saved,
                "recall": first,
                "reconstructed_recall": again,
                "derived_memories": memories,
            }
            (tmp_path / "business-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8"
            )
    finally:
        # Only the unique collection produced by this test's namespace is disposable.
        if not vectors.closed:
            vectors.client.drop_collection(vectors.collection, timeout=10)
