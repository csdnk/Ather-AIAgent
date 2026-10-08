"""Real Azure fixture factories with per-test ownership and no production fallbacks."""

import inspect
import os
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

import certifi
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from aether_agent_memory.remember.basic.ceph_p2 import CephP2Config
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.runtime.flows.host import ThreeFlows as RealThreeFlows
from aether_agent_memory.runtime.foundation.host import Foundation as RealFoundation
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresCapabilityStore,
    PostgresUnitOfWork,
)
from aether_agent_memory.runtime.foundation.postgres_telemetry import PostgresTelemetry
from aether_agent_memory.runtime.storage.objects import CephObjects
from aether_agent_memory.runtime.storage.redis_cache import RedisCache
from aether_agent_memory.runtime.storage.redis_executor import RedisExecutor
from aether_agent_memory.runtime.storage.vectors import AzureVectors
from controlled_embedding import ControlledEmbedding

_resources = ContextVar("test_owned_azure_resources", default=None)


class OwnedResources:
    def __init__(self, manifest=None):
        self.schemas = {}
        self.clients = []
        self.providers = {}
        self.namespace = "test-" + uuid4().hex
        self.provider_namespaces = {}
        if manifest:
            for key, schema in manifest["schemas"].items():
                if not schema.startswith("test_"):
                    raise ValueError("child test schema must be explicitly owned")
                self.schemas[key] = (
                    schema,
                    make_conninfo(
                        os.environ["P3_TEST_STATE_DSN"], options="-csearch_path=" + schema
                    ),
                )
            self.provider_namespaces.update(manifest["namespaces"])

    def manifest(self):
        return {
            "schemas": {key: schema for key, (schema, _) in self.schemas.items()},
            "namespaces": self.provider_namespaces,
        }

    def dsn(self, path):
        key = str(Path(path).resolve())
        if key not in self.schemas:
            dsn = os.environ.get("P3_TEST_STATE_DSN")
            if not dsn:
                raise RuntimeError("real Azure PostgreSQL requires P3_TEST_STATE_DSN")
            schema = "test_" + uuid4().hex
            with psycopg.connect(dsn, autocommit=True) as db:
                name = db.execute("SELECT current_database()").fetchone()[0]
                if not name.startswith("p3_test_"):
                    raise ValueError("test fixtures require a dedicated p3_test_ database")
                db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            self.schemas[key] = (schema, make_conninfo(dsn, options="-csearch_path=" + schema))
        return self.schemas[key][1]

    def close(self):
        errors = []
        # Service shutdown may already have closed its SDK clients. Keep the
        # exact test identities and clean them with independent connections.
        vectors = {
            (client.database, client.collection)
            for client in self.clients
            if isinstance(client, AzureVectors)
        }
        if vectors:
            from pymilvus import MilvusClient

            for database, collection in vectors:
                admin = None
                try:
                    admin = MilvusClient(
                        uri=os.environ["P3_TEST_MILVUS_URI"],
                        token=os.environ["P3_TEST_MILVUS_TOKEN"],
                        db_name=database,
                        secure=True,
                        server_pem_path=os.environ["P3_TEST_MILVUS_CA_FILE"],
                        server_name=os.environ["P3_TEST_MILVUS_SERVER_NAME"],
                    )
                    if admin.has_collection(collection_name=collection):
                        admin.drop_collection(collection_name=collection)
                except Exception as error:
                    errors.append(type(error).__name__)
                finally:
                    if admin is not None:
                        admin.close()
        for client in reversed(self.clients):
            try:
                if isinstance(client, CephObjects):
                    transport = client.transport.client
                    for page in transport.get_paginator("list_objects_v2").paginate(
                        Bucket=client.transport.bucket, Prefix=client.prefix
                    ):
                        for item in page.get("Contents", []):
                            transport.delete_object(Bucket=client.transport.bucket, Key=item["Key"])
                elif isinstance(client, RedisCache):
                    for key in client.client.scan_iter(match="aether:" + client.namespace + ":*"):
                        client.client.delete(key)
                if hasattr(client, "close"):
                    client.close()
            except Exception as error:
                errors.append(type(error).__name__)
        dsn = os.environ.get("P3_TEST_STATE_DSN")
        if dsn:
            with psycopg.connect(dsn, autocommit=True) as db:
                for schema, _ in self.schemas.values():
                    db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        if errors:
            raise RuntimeError("test-owned Azure cleanup failed: " + ", ".join(errors))


def owned():
    resources = _resources.get()
    if resources is None:
        raise RuntimeError("Azure factories must run inside the owned fixture")
    return resources


class Foundation(RealFoundation):
    __signature__ = inspect.signature(RealFoundation)

    def __init__(self, path, *args, **kwargs):
        if kwargs.get("uow") is None and kwargs.get("postgres_dsn") is None:
            kwargs["postgres_dsn"] = owned().dsn(path)
        super().__init__(path, *args, **kwargs)
        owned().clients.append(self)


class AzureRecords(PostgresCapabilityStore):
    def __init__(self, path):
        super().__init__(owned().dsn(path))
        owned().clients.append(self)


class AzureMetadata(PostgresUnitOfWork):
    def __init__(self, path):
        super().__init__(owned().dsn(path), path)
        owned().clients.append(self)


class Telemetry(PostgresTelemetry):
    def __init__(self, path, **kwargs):
        super().__init__(owned().dsn(str(path) + ".logs"), path, **kwargs)
        owned().clients.append(self)


def records(path):
    store = PostgresCapabilityStore(owned().dsn(path))
    owned().clients.append(store)
    return store


def metadata(path):
    store = PostgresUnitOfWork(owned().dsn(path), path)
    owned().clients.append(store)
    return store


def telemetry(path, **kwargs):
    logs = PostgresTelemetry(owned().dsn(str(path) + ".logs"), path, **kwargs)
    owned().clients.append(logs)
    return logs


def objects_for_path(path):
    return provider_options(path)["p2"]


def provider_options(path, policy=None):
    resources = owned()
    key = str(Path(path).resolve())
    if key in resources.providers:
        return resources.providers[key]
    import redis

    namespace = resources.provider_namespaces.setdefault(key, "test-" + uuid4().hex)
    client = redis.Redis(
        host=os.environ["P3_TEST_REDIS_HOST"],
        port=int(os.environ.get("P3_TEST_REDIS_PORT", "6380")),
        password=os.environ["P3_TEST_REDIS_PASSWORD"],
        ssl=True,
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
        ssl_ca_certs=os.environ.get("P3_TEST_REDIS_CA_FILE", certifi.where()),
        socket_timeout=10,
        socket_connect_timeout=10,
    )
    if not client.ping():
        raise RuntimeError("real Azure Redis is unavailable")
    resources.clients.append(client)
    cache = RedisCache(client, policy or RememberPolicy(), namespace=namespace)
    resources.clients.append(cache)
    endpoint = os.environ["P3_TEST_CEPH_ENDPOINT"]
    objects = CephObjects(
        CephP2Config(
            endpoint=endpoint,
            bucket=os.environ["P3_TEST_CEPH_BUCKET"],
            access_key_env="P3_TEST_CEPH_ACCESS",
            secret_key_env="P3_TEST_CEPH_SECRET",
        ),
        namespace=namespace,
        allow_insecure_http=os.environ.get("P3_TEST_CEPH_ALLOW_HTTP") == "1",
    )
    resources.clients.append(objects)

    def vector_factory(uow, identity, model_space, dimensions):
        vectors = AzureVectors(
            uow,
            identity,
            model_space,
            dimensions,
            namespace=namespace,
            collection="memories",
            uri=os.environ["P3_TEST_MILVUS_URI"],
            token=os.environ["P3_TEST_MILVUS_TOKEN"],
            database=os.environ["P3_TEST_MILVUS_DATABASE"],
            ca_file=os.environ["P3_TEST_MILVUS_CA_FILE"],
            server_name=os.environ["P3_TEST_MILVUS_SERVER_NAME"],
        )
        resources.clients.append(vectors)
        return vectors

    options = {
        "postgres_dsn": resources.dsn(path),
        "p2": objects,
        "body_cache": cache,
        "vectors_factory": vector_factory,
        "cache_factory": lambda uow, identity, memories: RedisExecutor(
            uow,
            identity,
            memories,
            cache,
            authority_reader=getattr(memories, "read_authority", None),
        ),
    }
    resources.providers[key] = options
    return options


def _runtime(factory, path, cache_root, **kwargs):
    supplied = dict(provider_options(path, kwargs.get("remember_policy")))
    if kwargs.get("uow") is not None:
        supplied.pop("postgres_dsn", None)
    if kwargs.get("redis") is not None:
        supplied.pop("body_cache", None)
    supplied.update(kwargs)
    supplied.setdefault("embedding", ControlledEmbedding())
    supplied["embedding_profile"] = (
        "injected" if supplied.get("embedding_config") is None else "native"
    )
    if supplied["embedding_profile"] == "native" and "embedding" not in kwargs:
        supplied.pop("embedding", None)
    result = factory(path, cache_root, **supplied)
    owned().clients.append(result)
    return result


def create_runtime(path, cache_root, **kwargs):
    from aether_agent_memory.remember.local import create_runtime as real_create_runtime

    return _runtime(real_create_runtime, path, cache_root, **kwargs)


class ThreeFlows(RealThreeFlows):
    def __init__(self, path, cache_root, **kwargs):
        supplied = dict(provider_options(path))
        supplied.pop("p2")
        supplied.pop("body_cache")
        supplied.update(kwargs)
        supplied.setdefault("embedding", ControlledEmbedding())
        supplied["embedding_profile"] = (
            "injected" if supplied.get("embedding_config") is None else "native"
        )
        if supplied["embedding_profile"] == "native" and "embedding" not in kwargs:
            supplied.pop("embedding", None)
        super().__init__(path, cache_root, **supplied)
        owned().clients.append(self)
