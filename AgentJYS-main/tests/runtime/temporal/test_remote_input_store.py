"""Remote immutable input must survive a worker directory disappearing."""

from hashlib import sha256

import pytest

from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, Principal, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.temporal.ingress import InputStore
from azure_test_runtime import create_runtime


class ImmutableObjects:
    def __init__(self):
        self.objects = {}
        self.failure = None

    def put_object_sync(self, key, body):
        if self.failure:
            raise self.failure
        if key in self.objects and self.objects[key] != body:
            raise ValueError("immutable key conflict")
        self.objects[key] = body

    def get_object_sync(self, key):
        if self.failure:
            raise self.failure
        return self.objects.get(key)


@pytest.fixture
def remote_inputs(tmp_path):
    runtime = create_runtime(tmp_path / "p3.db", tmp_path / "cache", embedding_profile="injected")
    principal = Principal(
        principal_id="alice",
        auth_epoch=1,
        permissions=tuple(Permission),
        home_scope=Scope(tenant_id="t1", application_id="app", user_id="alice", agent_id="agent"),
    )
    runtime.foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    provider = ImmutableObjects()
    store = InputStore(
        runtime.foundation.uow,
        runtime.foundation.identity,
        tmp_path / "worker-a-inputs",
        objects=provider,
    )
    yield runtime, provider, store
    runtime.close()


def test_remote_input_is_readable_from_a_different_worker_without_local_payload(
    remote_inputs, tmp_path
):
    runtime, provider, first = remote_inputs
    ctx = runtime.foundation.identity.context("alice", operation_id="input")
    body = "shared command bytes 中文".encode()
    ref = first.persist(ctx, "input", body, "application/json")
    second = InputStore(
        runtime.foundation.uow,
        runtime.foundation.identity,
        tmp_path / "worker-b-inputs",
        objects=provider,
    )
    assert second.read(ctx, ref) == body
    assert not list((tmp_path / "worker-a-inputs").glob("*"))
    assert not list((tmp_path / "worker-b-inputs").glob("*"))
    with runtime.foundation.uow.transaction() as tx:
        row = tx.get(ref)
    assert row["storage"] == "p2"
    assert provider.objects[row["object_key"]] == body


def test_remote_input_metadata_is_not_committed_before_verified_bytes(remote_inputs):
    runtime, provider, store = remote_inputs
    provider.failure = OSError("object service offline")
    ctx = runtime.foundation.identity.context("alice", operation_id="input")
    with pytest.raises(FoundationError) as error:
        store.persist(ctx, "input", b"bytes", "application/json")
    assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    with runtime.foundation.uow.transaction() as tx:
        assert tx.rows("records") == []


def test_remote_input_changed_bytes_are_rejected_and_original_remains(remote_inputs):
    runtime, provider, store = remote_inputs
    ctx = runtime.foundation.identity.context("alice", operation_id="input")
    ref = store.persist(ctx, "input", b"first", "application/json")
    with pytest.raises(FoundationError) as error:
        store.persist(ctx, "input", b"second", "application/json")
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert store.read(ctx, ref) == b"first"


def test_remote_input_corruption_is_reported_instead_of_using_local_fallback(remote_inputs):
    runtime, provider, store = remote_inputs
    ctx = runtime.foundation.identity.context("alice", operation_id="input")
    ref = store.persist(ctx, "input", b"first", "application/json")
    with runtime.foundation.uow.transaction() as tx:
        key = tx.get(ref)["object_key"]
    provider.objects[key] = b"altered"
    with pytest.raises(FoundationError) as error:
        store.read(ctx, ref)
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


def test_new_object_record_requires_binding_even_when_a_legacy_reader_is_present(remote_inputs):
    runtime, provider, store = remote_inputs
    ctx = runtime.foundation.identity.context("alice", operation_id="input")
    ref = store.persist(ctx, "input", b"first", "application/json")
    with runtime.foundation.uow.transaction() as tx:
        row = tx.get(ref)
        tx.put_if_revision(ref, {**row, "storage": "objects"}, tx.revision(ref))
    with pytest.raises(FoundationError) as error:
        store.read(ctx, ref)
    assert error.value.code == ErrorCode.VERSION_CONFLICT
