"""Buffered PostgreSQL commands keep transaction visibility and rollback contracts."""

import psycopg
import pytest
from test_postgres_observability import dsns as dsns

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import PostgresUnitOfWork


@pytest.fixture
def uow(tmp_path, dsns):
    value = PostgresUnitOfWork(dsns["state"], tmp_path / "anchor")
    yield value
    value.close()


def test_consecutive_writes_are_visible_to_get_scan_and_native_queue_queries(uow):
    with uow.transaction() as tx:
        for index in range(12):
            tx.write("tasks", str(index), {"record": {"state": "pending"}, "version": 1})
        assert len(tx.active_task_rows()) == 12
        for index in range(12):
            tx.write("tasks", str(index), {"record": {"state": "succeeded"}, "version": 2})
        assert tx.active_task_rows() == []
        assert len(tx.rows("tasks")) == 12
        assert tx.read("tasks", "0")["version"] == 2
    with uow.transaction() as tx:
        assert len(tx.rows_after("tasks", limit=20)) == 12
        assert tx.read("tasks", "0")["record"]["state"] == "succeeded"


def test_commit_guard_failure_rolls_back_every_prior_write(uow):
    def reject():
        raise FoundationError(ErrorCode.FORBIDDEN, "authority changed")

    with pytest.raises(FoundationError, match="authority changed"), uow.transaction() as tx:
        for index in range(12):
            tx.write("guarded", str(index), {"value": index})
        tx.before_commit.append(reject)
    with uow.transaction() as tx:
        assert tx.rows("guarded") == []


def test_deferred_sql_failure_rolls_back_preceding_and_following_commands(uow, dsns):
    with psycopg.connect(dsns["state"], autocommit=True) as db:
        db.execute(
            "ALTER TABLE capability_records ADD CONSTRAINT reject_test_key "
            "CHECK (key <> 'rejected')"
        )
    with pytest.raises(FoundationError) as error, uow.transaction() as tx:
        tx.write("delivery", "before", {"value": 1})
        tx.write("delivery", "rejected", {"value": 2})
        tx.write("delivery", "after", {"value": 3})
    assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    with uow.transaction() as tx:
        assert tx.rows("delivery") == []
        tx.write("delivery", "fresh", {"value": 4})
    with uow.transaction() as tx:
        assert tx.read("delivery", "fresh") == {"value": 4}


def test_repeated_authority_reads_do_not_repeat_network_queries(uow, monkeypatch):
    with uow.transaction() as tx:
        tx.write("authority", "principal", {"auth_epoch": 7})
    execute = psycopg.Connection.execute
    selects = []

    def observe(connection, query, parameters=None, **kwargs):
        if isinstance(query, str) and query.startswith("SELECT value FROM capability_records"):
            selects.append(parameters)
        return execute(connection, query, parameters, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", observe)
    with uow.transaction() as tx:
        for _ in range(20):
            assert tx.read("authority", "principal") == {"auth_epoch": 7}
            assert tx.read("authority", "missing") is None
    # Rechecking identical immutable inputs under the same shared transaction
    # lock must not spend 40 WAN round trips. A new transaction reads afresh.
    assert len(selects) == 2
    with uow.transaction() as tx:
        assert tx.read("authority", "principal") == {"auth_epoch": 7}
    assert len(selects) == 3


def test_repeated_reads_follow_put_delete_and_scope_without_aliasing(uow):
    with uow.transaction() as tx:
        raw = tx.raw
        assert raw.get("a", "t1", "same") is None
        raw.put("a", "t1", "same", "one")
        raw.put("a", "t2", "same", "two")
        raw.put("b", "t1", "same", "three")
        assert raw.get("a", "t1", "same") == "one"
        assert raw.get("a", "t2", "same") == "two"
        assert raw.get("b", "t1", "same") == "three"
        raw.delete("a", "t1", "same")
        assert raw.get("a", "t1", "same") is None
        raw.put("a", "t1", "same", "four")
        assert raw.get("a", "t1", "same") == "four"
        tx.write("values", "nested", {"items": [1]})
        tx.read("values", "nested")["items"].append(2)
        assert tx.read("values", "nested") == {"items": [1]}
    with pytest.raises(FoundationError, match="closed"):
        raw.get("a", "t1", "same")


def test_later_provider_changes_and_rollback_are_visible_to_new_transactions(uow, dsns, tmp_path):
    other = PostgresUnitOfWork(dsns["state"], tmp_path / "other")
    try:
        with uow.transaction() as tx:
            tx.write("authority", "principal", {"auth_epoch": 1})
            assert tx.read("authority", "principal") == {"auth_epoch": 1}
        with other.transaction() as tx:
            tx.write("authority", "principal", {"auth_epoch": 2})
        with pytest.raises(FoundationError), uow.transaction() as tx:
            assert tx.read("authority", "principal") == {"auth_epoch": 2}
            tx.write("authority", "principal", {"auth_epoch": 3})
            assert tx.read("authority", "principal") == {"auth_epoch": 3}
            tx.abort(ErrorCode.FORBIDDEN, "test rollback")
        with uow.transaction() as tx:
            assert tx.read("authority", "principal") == {"auth_epoch": 2}
    finally:
        other.close()


def test_commit_guard_observes_authority_change_after_earlier_read(uow):
    with uow.transaction() as tx:
        tx.write("authority", "principal", {"auth_epoch": 1})
    with pytest.raises(FoundationError, match="authority changed"), uow.transaction() as tx:
        assert tx.read("authority", "principal") == {"auth_epoch": 1}

        def guard():
            if tx.read("authority", "principal") != {"auth_epoch": 1}:
                tx.abort(ErrorCode.FORBIDDEN, "authority changed")

        tx.before_commit.append(guard)
        tx.write("authority", "principal", {"auth_epoch": 2})
        tx.write("guarded", "payload", {"value": "must roll back"})
    with uow.transaction() as tx:
        assert tx.read("authority", "principal") == {"auth_epoch": 1}
        assert tx.rows("guarded") == []
