"""Milvus boundary faults; controlled clients do not represent a live database."""

import asyncio
import os
from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest

from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.host import Foundation
from aether_agent_memory.runtime.vector_backend import MilvusConnection


@pytest.fixture
def foundation(tmp_path):
    host = Foundation(tmp_path / "foundation.db")
    principal = Principal(
        principal_id="alice",
        home_scope=Scope(tenant_id="t1", application_id="app", user_id="alice", agent_id="agent"),
        permissions=tuple(Permission),
        auth_epoch=1,
    )
    host.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    yield host
    host.close()


class SlowClient:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.second_entered = Event()
        self.finished = Event()
        self.timeouts = []

    def upsert(self, *, data, timeout, **kwargs):
        self.timeouts.append(timeout)
        if data == ["first"]:
            self.entered.set()
            assert self.release.wait(5), "test did not release SDK call"
            self.finished.set()
        else:
            self.second_entered.set()
        return data

    delete = upsert

    def query(self, **kwargs):
        return ["read while writing"]

    def close(self):
        pass


@pytest.fixture
def slow_connection(foundation):
    client = SlowClient()
    connection = MilvusConnection(
        foundation.uow, foundation.identity, "test", 2, uri="test", client=client
    )
    connection.serialize_writes = True
    yield connection, client
    client.release.set()
    connection.close()


async def timed_out_write(foundation, connection, client):
    ctx = foundation.identity.context("alice", timeout_seconds=0.15)
    task = asyncio.create_task(connection.call(ctx, "upsert", data=["first"]))
    assert await asyncio.to_thread(client.entered.wait, 2)
    with pytest.raises(FoundationError) as error:
        await task
    assert error.value.code == "DEPENDENCY_UNAVAILABLE"
    assert not client.finished.is_set()


@pytest.mark.parametrize("method", ["upsert", "delete"])
def test_serial_writes_remain_locked_after_await_timeout(foundation, slow_connection, method):
    connection, client = slow_connection

    async def scenario():
        await timed_out_write(foundation, connection, client)
        ctx = foundation.identity.context("alice", timeout_seconds=2)
        second = asyncio.create_task(connection.call(ctx, method, data=["second"]))
        try:
            assert await connection.call(ctx, "query") == ["read while writing"]
            await asyncio.sleep(0.08)
            assert not client.second_entered.is_set(), "SDK mutations overlapped after timeout"
        finally:
            client.release.set()
            await second
        assert client.finished.is_set() and client.second_entered.is_set()
        assert client.timeouts[1] < 1.95  # SDK budget is recomputed after the queue wait.

    asyncio.run(scenario())


@pytest.mark.parametrize("invalidated", ["expired", "revoked"])
def test_queued_write_is_revalidated_before_sdk_entry(foundation, slow_connection, invalidated):
    connection, client = slow_connection

    async def scenario():
        await timed_out_write(foundation, connection, client)
        ctx = foundation.identity.context(
            "alice", timeout_seconds=0.08 if invalidated == "expired" else 2
        )
        second = asyncio.create_task(connection.call(ctx, "upsert", data=["second"]))
        await asyncio.sleep(0.12)
        if invalidated == "revoked":
            foundation.identity.provision([])
        client.release.set()
        with pytest.raises(FoundationError):
            await second

    asyncio.run(scenario())
    connection.close()  # Wait for the underlying SDK thread, not only the asyncio task.
    assert not client.second_entered.is_set(), "invalid queued write reached the SDK"


def test_server_policy_allows_concurrent_writes_by_default(foundation, slow_connection):
    connection, client = slow_connection
    connection.serialize_writes = False

    async def scenario():
        await timed_out_write(foundation, connection, client)
        ctx = foundation.identity.context("alice")
        assert await connection.call(ctx, "upsert", data=["second"]) == ["second"]
        assert client.second_entered.is_set() and not client.finished.is_set()

    asyncio.run(scenario())


class CollectionClient:
    def __init__(self, fields, indexes=None):
        self.fields = fields
        self.indexes = {"vector": {"field_name": "vector", "metric_type": "IP"}}
        if indexes is not None:
            self.indexes = indexes
        self.loaded = False

    def has_collection(self, **kwargs):
        return True

    def describe_collection(self, **kwargs):
        return {"auto_id": False, "fields": self.fields}

    def list_indexes(self, **kwargs):
        return list(self.indexes)

    def describe_index(self, *, index_name, **kwargs):
        return self.indexes[index_name]

    def load_collection(self, **kwargs):
        self.loaded = True

    def close(self):
        pass


def collection_fields():
    from pymilvus import DataType

    return [
        {"name": "vector_id", "type": DataType.VARCHAR, "is_primary": True},
        {"name": "vector", "type": DataType.FLOAT_VECTOR, "params": {"dim": 2}},
        {"name": "target", "type": DataType.JSON},
        *[
            {"name": name, "type": DataType.VARCHAR}
            for name in (
                "model_space",
                "tenant_id",
                "application_id",
                "user_id",
                "agent_id",
                "session_id",
                "task_id",
            )
        ],
    ]


@pytest.mark.parametrize(
    "broken", ["vector_type", "target_type", "scalar_type", "primary", "metric", "no_index"]
)
def test_existing_collection_rejects_incompatible_contract(foundation, broken):
    from pymilvus import DataType

    fields = collection_fields()
    client = CollectionClient(fields)
    if broken == "vector_type":
        fields[1]["type"] = DataType.BINARY_VECTOR
    elif broken == "target_type":
        fields[2]["type"] = DataType.VARCHAR
    elif broken == "scalar_type":
        fields[3]["type"] = DataType.INT64
    elif broken == "primary":
        fields[0]["is_primary"] = False
        fields[3]["is_primary"] = True
    elif broken == "metric":
        client.indexes["vector"]["metric_type"] = "L2"
    else:
        client.indexes = {}
    connection = MilvusConnection(
        foundation.uow, foundation.identity, "test", 2, uri="test", client=client
    )
    try:
        with pytest.raises(FoundationError) as error:
            asyncio.run(connection.prepare(foundation.identity.context("alice")))
        assert error.value.code == "CONTRACT_VIOLATION"
        assert not connection.prepared and not client.loaded
    finally:
        connection.close()


def test_existing_compatible_collection_is_prepared(foundation):
    client = CollectionClient(collection_fields())
    connection = MilvusConnection(
        foundation.uow, foundation.identity, "test", 2, uri="test", client=client
    )
    try:
        asyncio.run(connection.prepare(foundation.identity.context("alice")))
        assert connection.prepared and client.loaded
    finally:
        connection.close()


@pytest.mark.integration
def test_real_lite_creates_and_reopens_collection_with_host_policy(tmp_path):
    pytest.importorskip("milvus_lite")
    from milvus_lite.server_manager import server_manager_instance

    from aether_agent_memory.recall.basic.config import RecallSettings
    from aether_agent_memory.runtime.flows.host import ThreeFlows

    directory = str(tmp_path)
    if os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        assert ctypes.windll.kernel32.GetShortPathNameW(directory, buffer, len(buffer))
        directory = buffer.value  # FAISS on Windows requires an ASCII index path.
    uri = str(Path(directory) / "vectors.db")
    host = None
    try:
        settings = RecallSettings(milvus_uri=uri, milvus_serialize_writes=True)
        host = ThreeFlows(
            tmp_path / "host.db",
            tmp_path / "cache",
            embedding_profile="lexical",
            recall_settings=settings,
        )
        principal = Principal(
            principal_id="alice",
            home_scope=Scope(
                tenant_id="t1", application_id="app", user_id="alice", agent_id="agent"
            ),
            permissions=tuple(Permission),
            auth_epoch=1,
        )
        host.foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
        connection = host.vectors
        assert connection.serialize_writes is True
        asyncio.run(connection.prepare(host.foundation.identity.context("alice")))
        assert connection.prepared
        indexes = connection.client.list_indexes(collection_name="p3_memories")
        assert indexes
        index = connection.client.describe_index(
            collection_name="p3_memories", index_name=indexes[0]
        )
        assert index["metric_type"] == "IP"
        # Re-enter validation against the existing real schema, with no data loss/rebuild.
        connection.prepared = False
        asyncio.run(connection.prepare(host.foundation.identity.context("alice")))
        assert connection.prepared
    finally:
        if host is not None:
            host.close()
        server_manager_instance.release_server(uri)
