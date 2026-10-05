from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from tests.unit.recall.helpers import binding, scope

from aether_agent_memory.p2.client import P2GrpcClient
from aether_agent_memory.p2.contracts import (
    P2CallContext,
    P2DeleteInput,
    P2OperationQueryInput,
    P2ProjectionMetadata,
    P2ProjectionPayload,
    P2ProjectionTarget,
    P2TargetQueryInput,
    P2UpsertInput,
)
from aether_agent_memory.p2.simulator import StatefulVectorSimulator
from aether_agent_memory.recall.vector_projection.models import ProviderResult
from aether_agent_memory.runtime.contract_types import (
    ByteRange,
    ProjectionIdentity,
    hash_bytes,
    hash_json,
    hash_text,
    utcnow,
    vector_bytes,
)
from azure_test_runtime import AzureRecords


def payload():
    meta = P2ProjectionMetadata(scope=scope(), memory_type="Semantic", occurred_at=None)
    return P2ProjectionPayload(
        usage="Passage",
        vector=[1, 2, 3],
        model_binding=binding(),
        source_hash=hash_text("input"),
        input_binding_digest=hash_json({"chunk": "one"}),
        vector_hash=hash_bytes(vector_bytes([1, 2, 3], 3, "float32")),
        representation_id="original",
        content_ref="body",
        content_version="v1",
        approved_range=ByteRange(start=0, end=5),
        metadata=meta,
        metadata_hash=hash_json(meta.model_dump(mode="json")),
    )


def target():
    return P2ProjectionTarget(
        tenant_id="tenant",
        provider_ref="simulator",
        retrieval_space_ref="space-v1",
        physical_target_ref=None,
        identity=ProjectionIdentity(
            memory_id="memory",
            chunk_id="chunk",
            memory_version="v1",
            model_version="v1",
            projection_schema_version="p1",
        ),
    )


def context():
    return P2CallContext(
        request_ref="request",
        trace_id="trace",
        tenant_id="tenant",
        provider_ref="simulator",
        contract_ref="simulator-contract",
        deadline_at=utcnow() + timedelta(seconds=10),
    )


async def test_simulator_lost_ack_query_visibility_and_delete_barrier(tmp_path):
    path = tmp_path / "state.db"
    store = AzureRecords(path)
    sim = StatefulVectorSimulator(store)
    request = P2UpsertInput(
        target=target(),
        provider_idempotency_key="write",
        request_fingerprint=hash_json({"write": 1}),
        payload=payload(),
        precondition_ref=None,
    )
    sim.lose_next_upsert_response = True
    with pytest.raises(TimeoutError):
        await sim.upsert(context(), request)
    store.close()
    store = AzureRecords(path)
    sim = StatefulVectorSimulator(store)
    _, operation = await sim.query_operation(
        context(),
        P2OperationQueryInput(
            target=target(),
            operation_kind="upsert",
            provider_operation_ref=None,
            provider_idempotency_key="write",
            request_fingerprint=request.request_fingerprint,
        ),
    )
    assert operation.operation_status == "accepted"
    assert (
        operation.target_state.object_present is True
        and operation.target_state.index_queryable is False
    )
    _, repeated = await sim.upsert(context(), request)
    assert repeated.provider_operation_ref == operation.provider_operation_ref
    sim.make_index_queryable(target())
    _, state = await sim.get_projection(
        context(),
        P2TargetQueryInput(
            target=target(),
            expected_request_fingerprint=None,
            related_operation_ref=None,
            include_payload=True,
        ),
    )
    assert state.index_queryable and state.stored_payload == request.payload
    delete = P2DeleteInput(
        target=target(),
        provider_idempotency_key="delete",
        request_fingerprint=hash_json({"delete": 1}),
        precondition_ref=None,
        related_upsert_keys=["write"],
    )
    _, deleted = await sim.delete_projection(context(), delete)
    assert (
        deleted.target_state.delete_confirmed and deleted.target_state.late_write_barrier_confirmed
    )
    _, late = await sim.upsert(context(), request)
    assert late.target_state.object_present is False
    with pytest.raises(ValueError, match="RETIRED"):
        await sim.upsert(context(), request.replaced(provider_idempotency_key="new-write"))
    store.close()


def test_provider_result_cannot_map_ack_to_ready():
    values = dict(
        schema_version="projection-data-0.2",
        tenant_id="tenant",
        created_at=utcnow(),
        result_id="r",
        operation_id="op",
        observation_version=1,
        operation_kind="upsert",
        identity=target().identity,
        provider_ref="simulator",
        retrieval_space_ref="space-v1",
        state="READY",
        raw_status="accepted",
        provider_operation_ref="remote-op",
        physical_target_ref=None,
        object_present=True,
        index_queryable=False,
        binding_evidence_ref=None,
        completion_evidence_ref=None,
        delete_confirmed=None,
        late_write_barrier_confirmed=None,
        error_code=None,
        retry_advice="none",
        safe_resubmit_evidence_ref=None,
        evidence_refs=[],
        observed_at=utcnow(),
        result_digest=hash_json({}),
    )
    with pytest.raises(ValidationError, match="READY requires"):
        ProviderResult(**values)




def test_python_metadata_retains_proto_optional_presence():
    class Meta:
        bucket = "b"
        key = "k"
        etag = "opaque"
        size = 8
        md5_hex = "a" * 32
        blake3_hex = ""

        def HasField(self, name):  # noqa: N802 - protobuf's public method spelling
            return name == "md5_hex"

    meta = P2GrpcClient._object_meta(Meta())
    assert meta.md5_hex == "a" * 32 and meta.blake3_hex is None


@pytest.mark.parametrize("has_meta", [False, True])
async def test_raw_object_read_preserves_metadata_and_legacy_bytes(has_meta):
    meta = SimpleNamespace(
        bucket="b",
        key="k",
        etag="opaque",
        size=3,
        md5_hex="a" * 32,
        blake3_hex="",
        HasField=lambda name: name == "md5_hex",
    )

    async def get_object(request, *, timeout):
        assert request.key == "k" and timeout == 1.25
        return SimpleNamespace(data=b"abc", meta=meta, HasField=lambda name: has_meta)

    client = P2GrpcClient("test-only", timeout_seconds=1.25)
    client._channel = object()
    client._pb = SimpleNamespace(GetObjectRequest=SimpleNamespace)
    client._grpc = SimpleNamespace(
        ObjectServiceStub=lambda channel: SimpleNamespace(GetObject=get_object)
    )
    result = await client.get_object_result("k")
    assert result.data == b"abc"
    if has_meta:
        assert result.meta.etag == "opaque" and result.meta.md5_hex == "a" * 32
        assert result.meta.blake3_hex is None
    else:
        assert result.meta is None
    assert await client.get_object("k") == b"abc"
