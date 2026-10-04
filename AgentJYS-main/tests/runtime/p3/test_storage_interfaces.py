"""An alternate storage implementation must retain actual foundation semantics."""

import hashlib
import inspect
import json

import pytest

from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError
from azure_test_runtime import AzureMetadata, Foundation, Telemetry


class RawRecords:
    """Minimal non-SQL store at the raw record protocol boundary."""

    def __init__(self):
        self.data = {}

    def get(self, namespace, tenant, key):
        return self.data.get((namespace, tenant, key))

    def put(self, namespace, tenant, key, value):
        self.data[namespace, tenant, key] = value

    def delete(self, namespace, tenant, key):
        self.data.pop((namespace, tenant, key), None)

    def scan(self, namespace):
        return [(t, k, v) for (n, t, k), v in self.data.items() if n == namespace]


def test_non_sql_storage_supports_business_transaction_and_pending_selection():
    from aether_agent_memory.runtime.foundation import transactions as storage

    assert hasattr(storage, "StorageTransaction"), (
        "shared record semantics require a neutral adapter"
    )
    raw = RawRecords()
    tx = storage.StorageTransaction(raw, "cursor-secret")
    assert storage.native(tx) is tx
    tx.write("tasks", "first", {"record": {"state": "pending"}})
    tx.write("tasks", "done", {"record": {"state": "completed"}})
    tx.write("temporal_start_intents", "start", {"state": "pending"})
    tx.write("temporal_start_intents", "sent", {"state": "sent"})
    assert tx.active_task_rows() == [("first", {"record": {"state": "pending"}})]
    assert tx.pending_intent_rows("start") == [("start", {"state": "pending"})]
    assert json.loads(raw.get("p3_rf_tasks", "system", "first")) == {"record": {"state": "pending"}}
    tx.open = False
    with pytest.raises(FoundationError):
        storage.native(tx)


class DelegatingStore:
    """A distinct provider using the reference store only as unit-test persistence."""

    def __init__(self, path):
        self.delegate = AzureMetadata(path)
        self.path = self.delegate.path
        self.store = self.delegate.store
        self.backend = "test_contract"
        self.telemetry = None

    def transaction(self):
        return self.delegate.transaction()

    def close(self):
        self.delegate.close()


def person():
    return Principal(
        principal_id="alice",
        home_scope=Scope(tenant_id="t1", application_id="app", user_id="alice", agent_id="agent"),
        permissions=tuple(Permission),
        auth_epoch=1,
    )


def open_external(tmp_path):
    assert "uow" in inspect.signature(Foundation).parameters, (
        "Foundation must accept a storage interface instead of constructing its own database"
    )
    uow = DelegatingStore(tmp_path / "test-adapter.db")
    telemetry = Telemetry(tmp_path / "test-adapter.logs.db")
    return Foundation(tmp_path / "must-not-exist.db", uow=uow, telemetry=telemetry)


def test_external_provider_preserves_business_identity_and_persistence(tmp_path):
    host = open_external(tmp_path)
    principal = person()
    try:
        host.identity.provision([(hashlib.sha256(b"alice").hexdigest(), principal)])
        ctx = host.identity.context("alice")
        assert ctx.principal == principal
        with host.uow.transaction() as tx:
            tx.write("test", "original", {"operation_id": "original-op"})
    finally:
        host.close()
    host = open_external(tmp_path)
    try:
        assert host.identity.context("alice").principal == principal
        with host.uow.transaction() as tx:
            assert tx.read("test", "original") == {"operation_id": "original-op"}
    finally:
        host.close()
    assert not (tmp_path / "must-not-exist.db").exists()


def test_external_provider_preserves_guard_rollback_and_closed_transaction(tmp_path):
    host = open_external(tmp_path)
    try:

        def reject():
            raise RuntimeError("ownership changed")

        with pytest.raises(RuntimeError, match="ownership changed"), host.uow.transaction() as tx:
            tx.write("facts", "original", {"v": 1})
            tx.write("outbox", "original", {"v": 1})
            tx.before_commit.append(reject)
        with pytest.raises(FoundationError):
            tx.read("facts", "original")
        with host.uow.transaction() as tx:
            assert tx.read("facts", "original") is None
            assert tx.read("outbox", "original") is None
    finally:
        host.close()


def test_external_provider_requires_matching_explicit_telemetry(tmp_path):
    assert "uow" in inspect.signature(Foundation).parameters
    uow = DelegatingStore(tmp_path / "test-adapter.db")
    try:
        with pytest.raises(ValueError, match="together"):
            Foundation(tmp_path / "must-not-exist.db", uow=uow)
    finally:
        uow.close()
    assert not (tmp_path / "must-not-exist.db").exists()
