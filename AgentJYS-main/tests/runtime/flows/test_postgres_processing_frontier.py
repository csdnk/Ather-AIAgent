"""Processing frontier query preserves task history and batch membership."""

import pytest
from test_postgres_observability import dsns as dsns

from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction,
    PostgresUnitOfWork,
    _PostgresRecordTransaction,
)


@pytest.mark.parametrize("ids", [("",), (None,), ("x",) * 1001])
def test_processing_frontier_rejects_unbounded_or_invalid_ids_without_database_io(ids):
    tx = PostgresTransaction(_PostgresRecordTransaction(None), "key")
    with pytest.raises(ValueError):
        tx.processing_tasks_for_memories(ids)


def test_empty_processing_frontier_never_queries_and_still_checks_transaction():
    tx = PostgresTransaction(_PostgresRecordTransaction(None), "key")
    assert tx.processing_tasks_for_memories(()) == []
    tx.open = False
    with pytest.raises(FoundationError):
        tx.processing_tasks_for_memories(())


def test_processing_frontier_returns_direct_and_batch_tasks_in_all_states(tmp_path, dsns):
    uow = PostgresUnitOfWork(dsns["state"], tmp_path / "frontier")
    try:
        with uow.transaction() as tx:
            for key, memory_id, state in [
                ("old", "root", "failed"),
                ("direct", "root", "succeeded"),
                ("batch", "other", "cancelled"),
                ("next", "child", "pending"),
                ("unrelated", "other", "running"),
            ]:
                tx.write(
                    "tasks",
                    key,
                    {
                        "record": {
                            "task_id": key,
                            "subject": {"object_id": memory_id},
                            "state": state,
                            "result_ref": {"object_id": "result-" + key},
                        },
                        "lease_evidence": {"attempt": 2},
                    },
                )
            tx.write(
                "remember_batches",
                "batch",
                {
                    "refs": [
                        {"memory_id": "root"},
                        {"memory_id": "root"},
                        {"memory_id": "other"},
                    ]
                },
            )
            tx.write("remember_batches", "unrelated", {"refs": [{"memory_id": "else"}]})
        with uow.transaction() as tx:
            rows = tx.processing_tasks_for_memories(("root",))
            assert [key for key, _ in rows] == ["batch", "direct", "old"]
            assert [value["record"]["state"] for _, value in rows] == [
                "cancelled",
                "succeeded",
                "failed",
            ]
            assert all(value["lease_evidence"] == {"attempt": 2} for _, value in rows)
            assert rows[0][1]["record"]["result_ref"] == {"object_id": "result-batch"}
            assert [key for key, _ in tx.processing_tasks_for_memories(("child",))] == ["next"]
            assert tx.processing_tasks_for_memories(("missing' OR TRUE --",)) == []
            assert [key for key, _ in tx.processing_tasks_for_memories(("root", "child"))] == [
                "batch",
                "direct",
                "next",
                "old",
            ]
    finally:
        uow.close()
