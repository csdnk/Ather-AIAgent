"""Real PG/Redis/Milvus composition with controlled Ceph responses, not release acceptance."""

import asyncio
import json
import os
import sqlite3
from hashlib import sha256

import pytest
from botocore.stub import ANY, Stubber
from psycopg.conninfo import conninfo_to_dict
from test_azure_objects import response
from test_azure_storage_configuration import azure_settings
from test_postgres_observability import dsns as dsns
from test_postgres_observability import people

from aether_agent_memory.runtime.flows.config import ServiceConfiguration
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.storage.azure import StorageProviders
from aether_agent_memory.runtime.temporal.ingress import InputStore
from azure_storage_support import azure_redis as azure_redis
from azure_test_runtime import create_runtime
from controlled_embedding import ControlledEmbedding as LexicalEmbedding


def configured(tmp_path, dsns, redis, monkeypatch):
    client, namespace = redis
    value = azure_settings(tmp_path)
    config = value["azure_storage"]
    config["namespace"] = namespace
    config["postgres"]["schema_name"] = conninfo_to_dict(dsns["state"])["options"].split("=", 1)[1]
    config["redis"].update(
        host=os.environ["P3_TEST_REDIS_HOST"],
        port=int(os.environ["P3_TEST_REDIS_PORT"]),
        ca_file=os.environ["P3_TEST_REDIS_CA_FILE"],
    )
    config["milvus"].update(
        uri=os.environ["P3_TEST_MILVUS_URI"],
        database=os.environ["P3_TEST_MILVUS_DATABASE"],
        ca_file=os.environ["P3_TEST_MILVUS_CA_FILE"],
        server_name=os.environ["P3_TEST_MILVUS_SERVER_NAME"],
    )
    monkeypatch.setenv("TEST_P3_POSTGRES_DSN", dsns["state"])
    monkeypatch.setenv("TEST_REDIS_PASSWORD", os.environ["P3_TEST_REDIS_PASSWORD"])
    monkeypatch.setenv("TEST_MILVUS_TOKEN", os.environ["P3_TEST_MILVUS_TOKEN"])
    monkeypatch.setenv("TEST_CEPH_ACCESS", "contract-access")
    monkeypatch.setenv("TEST_CEPH_SECRET", "contract-secret")
    return ServiceConfiguration.model_validate(value)


def test_complete_provider_bundle_constructs_runtime_without_opening_sqlite(
    tmp_path, dsns, azure_redis, monkeypatch
):
    config = configured(tmp_path, dsns, azure_redis, monkeypatch)

    def no_sqlite(*args, **kwargs):
        raise AssertionError("Azure assembly must never open SQLite")

    monkeypatch.setattr(sqlite3, "connect", no_sqlite)
    providers = StorageProviders(config.azure_storage, tmp_path / "anchors", config.remember)
    runtime = None
    try:
        runtime = create_runtime(
            tmp_path / "unused.db",
            tmp_path / "cache",
            embedding_profile="injected",
            **providers.runtime_options(),
        )
        providers.transfer_runtime_ownership()
        people(runtime.foundation)
        ctx = runtime.foundation.identity.context("alice", timeout_seconds=300)
        location = runtime.remember.bodies.location(ctx.principal.home_scope, "body")
        assert location.provider_id == "ceph"
        assert runtime.remember.bodies.remote_only
        assert runtime.remember.bodies.cache is providers.cache
        assert runtime.executor.provider_id == "redis_hot_cache"
        assert runtime.foundation.uow.backend == "postgresql"
        with runtime.foundation.uow.transaction() as tx:
            assert (
                tx.read("settings", "p3_vector_binding")["namespace"]
                == config.azure_storage.namespace
            )
        providers.cache.put_sync(ctx.principal.home_scope, "body")
        assert (
            runtime.executor.read_cached(ctx.principal.home_scope, sha256(b"body").hexdigest())
            == "body"
        )

        objects = providers.objects
        payload = b"original temporal input"
        with Stubber(objects.transport.client) as stub:
            stub.add_response(
                "put_object",
                {},
                {
                    "Bucket": "p3-memory",
                    "Key": ANY,
                    "Body": payload,
                    "ContentLength": len(payload),
                    "ContentType": "application/octet-stream",
                    "ContentMD5": ANY,
                    "Metadata": {"sha256": sha256(payload).hexdigest()},
                    "IfNoneMatch": "*",
                },
            )
            for _ in range(3):
                stub.add_response(
                    "get_object", response(payload), {"Bucket": "p3-memory", "Key": ANY}
                )
            store = InputStore(
                runtime.foundation.uow,
                runtime.foundation.identity,
                tmp_path / "input-a",
                objects=objects,
            )
            ref = store.persist(ctx, "original", payload, "application/octet-stream")
            restored = InputStore(
                runtime.foundation.uow,
                runtime.foundation.identity,
                tmp_path / "input-b",
                objects=objects,
            )
            assert restored.read(ctx, ref) == payload
            with runtime.foundation.uow.transaction() as tx:
                saved = tx.get(ref)
            assert saved["storage"] == "objects"
            assert saved["object_binding"]["provider"] == "ceph"
            assert saved["object_binding"]["namespace"] == config.azure_storage.namespace
            stub.assert_no_pending_responses()
        assert not (tmp_path / "unused.db").exists()
        assert not list((tmp_path / "input-a").iterdir())
        assert not list((tmp_path / "input-b").iterdir())
    finally:
        if runtime is not None:
            runtime.close()
        providers.close()


def test_failed_runtime_attachment_releases_real_metadata_and_log_connections(
    tmp_path, dsns, azure_redis, monkeypatch
):
    from psycopg_pool import PoolClosed

    from aether_agent_memory.remember.local import RememberFactory

    config = configured(tmp_path, dsns, azure_redis, monkeypatch)
    providers = StorageProviders(config.azure_storage, tmp_path / "anchors", config.remember)

    def failed_attachment(self, runtime):
        raise ValueError("invalid recall attachment")

    monkeypatch.setattr(RememberFactory, "attach", failed_attachment)
    try:
        with pytest.raises(ValueError, match="invalid recall attachment"):
            create_runtime(
                tmp_path / "unused.db",
                tmp_path / "cache",
                embedding_profile="injected",
                **providers.runtime_options(),
            )
        with pytest.raises(FoundationError, match="closed"), providers.metadata.transaction():
            pass
        with pytest.raises(PoolClosed), providers.telemetry.reader():
            pass
    finally:
        providers.close()


def test_azure_service_assembles_actual_providers_and_closes_them(
    tmp_path, dsns, azure_redis, monkeypatch
):
    from aether_agent_memory.runtime.flows.application import Service

    config = configured(tmp_path, dsns, azure_redis, monkeypatch)
    config.identity_file.write_text(
        json.dumps(
            {"revision": 1, "tenants": [{"tenant_id": "t1"}, {"tenant_id": "t2"}], "identities": []}
        )
    )
    service = Service(config, embedding=LexicalEmbedding())
    try:
        people(service.runtime.foundation)
        ctx = service.runtime.foundation.identity.context("alice", timeout_seconds=300)
        assert service.execution.inputs.objects is service.storage_providers.objects
        assert service.runtime.remember.bodies.provider_id == "ceph"
        assert service.runtime.executor.probe()["tiers"] == ["hot"]
        assert service.runtime.foundation.uow.backend == "postgresql"
        assert not list(config.data_dir.rglob("*.db"))
        assert asyncio.run(service.runtime.executor.resources(ctx)).supported_moves == ()
    finally:
        asyncio.run(service.close())
    with (
        pytest.raises(FoundationError, match="closed"),
        service.runtime.foundation.uow.transaction(),
    ):
        pass
    assert service.storage_providers.closed
