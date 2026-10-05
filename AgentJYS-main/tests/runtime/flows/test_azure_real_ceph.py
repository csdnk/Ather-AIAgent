"""Opt-in real RGW contract checks confined to a unique test namespace."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from aether_agent_memory.remember.basic.ceph_p2 import CephP2Config
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.storage.objects import CephObjects

pytestmark = pytest.mark.integration


@pytest.fixture
def real_ceph():
    if os.environ.get("P3_REQUIRE_CEPH") != "1":
        pytest.skip("explicit real Ceph configuration required")
    endpoint = os.environ["P3_TEST_CEPH_ENDPOINT"]
    bucket = os.environ["P3_TEST_CEPH_BUCKET"]
    namespace = "acceptance-" + uuid4().hex
    config = CephP2Config(
        endpoint=endpoint,
        bucket=bucket,
        access_key_env="P3_TEST_CEPH_ACCESS",
        secret_key_env="P3_TEST_CEPH_SECRET",
        ca_file=os.environ.get("P3_TEST_CEPH_CA_FILE"),
    )
    objects = CephObjects(
        config, namespace=namespace,
        allow_insecure_http=os.environ.get("P3_TEST_CEPH_ALLOW_HTTP") == "1",
    )
    try:
        yield objects
    finally:
        # Delete only objects inside this fixture's freshly minted prefix.
        client = objects.transport.client
        try:
            pages = client.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=objects.prefix
            )
            for page in pages:
                for item in page.get("Contents", []):
                    assert item["Key"].startswith(objects.prefix)
                    client.delete_object(Bucket=bucket, Key=item["Key"])
        finally:
            objects.close()


def test_real_ceph_preserves_immutable_bytes_range_and_idempotent_delete(real_ceph):
    original = "真实 Ceph 原始输入：预算 150000 元。\n".encode()
    key = "bodies/original"
    assert real_ceph.get_object_sync(key) is None
    real_ceph.put_object_sync(key, original)
    real_ceph.put_object_sync(key, original)
    assert real_ceph.get_object_sync(key) == original
    assert real_ceph.read_range_sync(key, 3, 20) == original[3:20]
    assert real_ceph.read_range_sync(key, 4, 4) == b""
    with pytest.raises(FoundationError) as error:
        real_ceph.put_object_sync(key, b"must not overwrite")
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert real_ceph.get_object_sync(key) == original
    real_ceph.delete_object_sync(key)
    real_ceph.delete_object_sync(key)
    assert real_ceph.get_object_sync(key) is None


def test_real_ceph_concurrent_different_payloads_have_one_winner(real_ceph):
    payloads = [f"candidate-{index}".encode() for index in range(8)]

    def write(body):
        try:
            real_ceph.put_object_sync("concurrency/original", body)
            return body, None
        except FoundationError as error:
            return body, error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, payloads))
    winners = [body for body, error in results if error is None]
    assert len(winners) == 1
    assert all(error in {None, ErrorCode.IDEMPOTENCY_CONFLICT} for _, error in results)
    assert real_ceph.get_object_sync("concurrency/original") == winners[0]


@pytest.mark.parametrize("damage", ["bytes", "missing_hash"])
def test_real_ceph_corrupt_stored_object_is_rejected(real_ceph, damage):
    # The fault is an actual write to this test's object, never shared data.
    original = b"integrity-checked original"
    metadata = {"sha256": hashlib.sha256(original).hexdigest()} if damage == "bytes" else {}
    real_ceph.transport.client.put_object(
        Bucket=real_ceph.transport.bucket,
        Key=real_ceph.prefix + "corrupt/object",
        Body=b"damaged" if damage == "bytes" else original,
        Metadata=metadata,
    )
    with pytest.raises(FoundationError) as error:
        real_ceph.get_object_sync("corrupt/object")
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


def test_real_ceph_confirmed_write_survives_lost_response(real_ceph, monkeypatch):
    write = real_ceph.transport.put_object_sync

    def lose_response(key, body):
        write(key, body)
        raise TimeoutError("test fault after the actual RGW write")

    monkeypatch.setattr(real_ceph.transport, "put_object_sync", lose_response)
    real_ceph.put_object_sync("uncertain/original", b"original committed input")
    assert real_ceph.get_object_sync("uncertain/original") == b"original committed input"


def test_real_ceph_new_client_reads_same_namespace_and_isolates_another(real_ceph):
    real_ceph.put_object_sync("shared/original", b"persistent bytes")
    same = CephObjects(
        real_ceph.transport.config, namespace=real_ceph.namespace,
        allow_insecure_http=os.environ.get("P3_TEST_CEPH_ALLOW_HTTP") == "1",
    )
    other = CephObjects(
        real_ceph.transport.config, namespace="acceptance-" + uuid4().hex,
        allow_insecure_http=os.environ.get("P3_TEST_CEPH_ALLOW_HTTP") == "1",
    )
    try:
        assert same.get_object_sync("shared/original") == b"persistent bytes"
        assert other.get_object_sync("shared/original") is None
    finally:
        same.close()
        other.close()
