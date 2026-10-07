from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from aether_agent_memory.runtime.flows.dashboard import memory_observations
from aether_platform.operations.performance import performance


class Connection:
    def __init__(self):
        self.queries = []

    def execute(self, query, params):
        self.queries.append((query, params))
        return self

    def fetchall(self):
        return []


def test_performance_empty_windows_keep_latency_unknown_and_scope_every_query():
    conn = Connection()

    @contextmanager
    def connection():
        yield conn

    result = performance(
        SimpleNamespace(connection=connection), SimpleNamespace(role="tenant_admin", tenant_id="a")
    )
    assert set(result["windows"]) == {"1h", "24h", "7d"}
    for value in result["windows"].values():
        assert value["summary"]["recall_return_p95_ms"] is None
        assert value["summary"]["success_rate"] is None
        assert value["summary"]["requests"] == 0
    assert all(False in args and "a" in args for _, args in conn.queries)
    assert all("c.tenant_id=%s" in query for query, _ in conn.queries)


def test_memory_observations_include_only_deployment_tasks_and_safe_fields():
    class Tx:
        def rows(self, table):
            return iter(
                {
                    "tasks": [],
                    "remember_artifacts": [
                        (
                            "a",
                            {
                                "task_id": "ours",
                                "published": True,
                                "quality": "passed",
                                "original_bytes": 1000,
                                "stored_bytes": 200,
                                "text": "secret",
                            },
                        ),
                        (
                            "b",
                            {
                                "task_id": "foreign",
                                "published": True,
                                "quality": "passed",
                                "original_bytes": 99999,
                                "stored_bytes": 1,
                            },
                        ),
                    ],
                    "operate_task_actions": [
                        ("ours", {"action_id": "move"}),
                        ("foreign", {"action_id": "foreign"}),
                    ],
                }[table]
            )

        def read(self, table, key):
            return {
                "state": "unknown",
                "intent": {
                    "created_at": "2026-10-06T00:00:00Z",
                    "decision": {
                        "outcome": "promote",
                        "current_tier": "warm",
                        "target_tier": "hot",
                        "reason": "secret",
                    },
                },
                "feedback": {"state": "unknown", "token": "secret"},
            }

    result = memory_observations(Tx(), {"ours"}, datetime.now(UTC).isoformat())
    assert result["compression"]["ratio"] == 5
    assert result["compression"]["samples"] == 1
    assert result["placement"]["total"] == 1
    assert result["placement"]["items"][0]["state"] == "unknown"
    assert "secret" not in str(result)
    assert "foreign" not in str(result)


def test_unpublished_or_invalid_compression_is_not_zero_or_success():
    class Tx:
        def rows(self, table):
            return iter(
                [
                    (
                        "a",
                        {
                            "task_id": "ours",
                            "published": False,
                            "original_bytes": 1000,
                            "stored_bytes": 0,
                        },
                    )
                ]
                if table == "remember_artifacts"
                else []
            )

    result = memory_observations(Tx(), {"ours"}, "now")
    assert result["compression"]["ratio"] is None
    assert result["compression"]["status"] == "no_samples"
