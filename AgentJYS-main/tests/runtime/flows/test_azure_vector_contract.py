"""Real PG intents and Azure Milvus projections; never substitutes a flat index."""

import os
from uuid import uuid4

import pytest
from test_postgres_observability import dsns as dsns
from test_postgres_observability import open_host, people

from aether_agent_memory.recall.contracts.models import VectorSearchRequest
from aether_agent_memory.remember.basic.projection import projection_target
from aether_agent_memory.remember.contracts.models import MemoryRef, ProjectionRequest
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash

SPACE = "azure_contract_vectors_v1"
pytestmark = pytest.mark.integration


@pytest.fixture
def vector_resources(tmp_path, dsns):
    uri = os.environ.get("P3_TEST_MILVUS_URI")
    if not uri:
        if os.environ.get("P3_REQUIRE_MILVUS") == "1":
            pytest.fail("real Milvus is required")
        pytest.skip("real Milvus configuration is required")
    host = open_host(tmp_path, dsns)
    people(host)
    owned = []
    collection = "contract_" + uuid4().hex

    def make(namespace):
        from aether_agent_memory.runtime.storage.vectors import AzureVectors

        vector = AzureVectors(
            host.uow,
            host.identity,
            SPACE,
            4,
            namespace=namespace,
            collection=collection,
            uri=uri,
            token=os.environ["P3_TEST_MILVUS_TOKEN"],
            ca_file=os.environ["P3_TEST_MILVUS_CA_FILE"],
            server_name=os.environ["P3_TEST_MILVUS_SERVER_NAME"],
            database=os.environ["P3_TEST_MILVUS_DATABASE"],
        )
        owned.append(vector)
        return vector

    yield host, make
    try:
        for vector in reversed(owned):
            try:
                # Only fresh test-owned collections, never a database or shared collection.
                if vector.client.has_collection(vector.collection, timeout=10):
                    vector.client.drop_collection(vector.collection, timeout=10)
            finally:
                vector.close()
    finally:
        host.close()


def target(ctx, name, source="working"):
    digest = text_hash(name)
    return projection_target(
        MemoryRef(scope=ctx.principal.home_scope, memory_id=name, version=1),
        digest,
        SPACE,
        generation="generation1",
        body_hash=digest,
        memory_source=source,
    )


def request(ctx, value):
    return ProjectionRequest(
        operation_id="project_" + value.memory.memory_id,
        target=value,
        vector=(1, 0, 0, 0),
        deadline_at=ctx.deadline_at,
    )


async def search(vector, ctx, source="working"):
    return await vector.search(
        ctx,
        VectorSearchRequest(
            selection={},
            memory_source=source,
            vector=(1, 0, 0, 0),
            model_space=SPACE,
            limit=10,
            deadline_at=ctx.deadline_at,
        ),
    )


async def test_real_projection_replay_environment_isolation_and_reconstruction(vector_resources):
    host, make = vector_resources
    first, other = make("test-a"), make("test-b")
    ctx = host.identity.context("alice", timeout_seconds=300)
    value = target(ctx, "working1")
    projected = await first.project(ctx, request(ctx, value))
    assert projected.state == "verified" and projected.searchable
    assert (await first.project(ctx, request(ctx, value))).state == "verified"
    assert (await search(other, ctx)).candidates == ()
    assert (await other.project(ctx, request(ctx, value))).state == "verified"
    recreated = make("test-a")
    assert recreated.binding() == first.binding()
    assert recreated.binding() != other.binding()
    found = await search(recreated, ctx)
    assert [item.target for item in found.candidates] == [value]
    changed = request(ctx, value).model_copy(update={"vector": (0, 1, 0, 0)})
    with pytest.raises(FoundationError) as error:
        await recreated.project(ctx, changed)
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert (await recreated.delete(ctx, value, "delete-original")).state == "absent"
    with pytest.raises(FoundationError) as error:
        await first.project(ctx, request(ctx, value))
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert (await search(first, ctx)).candidates == ()
    assert (await other.inspect(ctx, value, "other-original")).state == "verified"


async def test_real_search_filters_source_and_scope_and_rejects_corrupt_payload(vector_resources):
    host, make = vector_resources
    vector = make("test-filter")
    alice = host.identity.context("alice", timeout_seconds=300)
    bob = host.identity.context("bob", timeout_seconds=300)
    working, longterm, foreign = (
        target(alice, "working"),
        target(alice, "longterm", "long_term"),
        target(bob, "foreign"),
    )
    for ctx, value in [(alice, working), (alice, longterm), (bob, foreign)]:
        assert (await vector.project(ctx, request(ctx, value))).state == "verified"
    assert [item.target for item in (await search(vector, alice)).candidates] == [working]
    assert [item.target for item in (await search(vector, alice, "long_term")).candidates] == [
        longterm
    ]
    assert [item.target for item in (await search(vector, bob)).candidates] == [foreign]
    vector.client.upsert(
        collection_name=vector.collection,
        data=[
            {
                "vector_id": working.vector_id,
                "vector": [0, 1, 0, 0],
                "target": working.model_dump(mode="json"),
                "model_space": SPACE,
                **{key: value or "" for key, value in working.memory.scope.model_dump().items()},
            }
        ],
        timeout=10,
    )
    assert (await vector.inspect(alice, working, "inspect-original")).state == "failed"


def test_azure_vectors_rejects_unverified_transport_before_opening_connections():
    from aether_agent_memory.runtime.storage.vectors import AzureVectors

    with pytest.raises(ValueError, match="HTTPS"):
        AzureVectors(
            None,
            None,
            SPACE,
            4,
            namespace="test",
            collection="contract_test",
            uri="http://milvus.internal:19530",
            token="",
            ca_file=None,
            server_name="milvus.internal",
            database="p3_test",
        )
