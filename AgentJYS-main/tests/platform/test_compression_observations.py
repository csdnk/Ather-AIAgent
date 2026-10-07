import pytest

from aether_agent_memory.runtime.flows.dashboard import memory_observations


class Tx:
    def __init__(self, tasks=(), artifacts=()):
        self.tables = {
            "tasks": list(tasks),
            "remember_artifacts": list(artifacts),
            "operate_task_actions": [],
        }

    def rows(self, table):
        return iter(self.tables[table])


def task(task_id, state, kind="remember.compress"):
    return task_id, {"record": {"kind": kind, "state": state}}


def artifact(task_id, **changes):
    return task_id, {
        "task_id": task_id,
        "published": True,
        "quality": "passed",
        "original_bytes": 1000,
        "stored_bytes": 200,
        **changes,
    }


def observe(tasks=(), artifacts=(), task_ids=None):
    if task_ids is None:
        task_ids = {key for key, _ in tasks} | {key for key, _ in artifacts}
    return memory_observations(Tx(tasks, artifacts), task_ids, "now")["compression"]


def test_no_compression_tasks_is_not_triggered_even_when_other_tasks_exist():
    result = observe([task("project", "succeeded", "remember.project")])
    assert result["status"] == "no_samples"
    assert result["reason"] == "not_triggered"
    assert result["task_total"] == result["artifacts_total"] == 0
    assert result["tasks_by_state"] == {}
    assert result["ratio"] is result["original_bytes"] is result["stored_bytes"] is None


@pytest.mark.parametrize("state", ["pending", "running", "retry_wait", "recovery_wait"])
def test_active_compression_is_pending_even_with_older_failed_task(state):
    result = observe([task("active", state), task("old", "failed")])
    assert result["reason"] == "pending"
    assert result["tasks_by_state"] == {state: 1, "failed": 1}
    assert result["task_total"] == 2


@pytest.mark.parametrize(
    "state,reason",
    [
        ("failed", "failed"),
        ("succeeded", "no_published_artifacts"),
        ("cancelled", "no_published_artifacts"),
        ("attention_required", "no_published_artifacts"),
        (None, "no_published_artifacts"),
    ],
)
def test_terminal_or_unknown_tasks_do_not_invent_published_samples(state, reason):
    result = observe([task("ours", state)])
    assert result["reason"] == reason
    assert result["tasks_by_state"] == {state or "unknown": 1}
    assert result["samples"] == 0


def test_artifact_without_visible_task_is_not_misreported_as_never_triggered():
    result = observe(artifacts=[artifact("ours", published=False)])
    assert result["reason"] == "no_published_artifacts"
    assert result["artifacts_total"] == result["unpublished_artifacts"] == 1


def test_counts_retain_independent_rejection_reasons_and_deployment_scope():
    tasks = [task("ours", "succeeded"), task("foreign", "running")]
    artifacts = [
        artifact("ours", published=False, quality="failed", stored_bytes=0),
        artifact("ours", published=True, quality="pending"),
        artifact("foreign"),
    ]
    result = observe(tasks, artifacts, {"ours"})
    assert result["reason"] == "no_published_artifacts"
    assert result["task_total"] == 1
    assert result["tasks_by_state"] == {"succeeded": 1}
    assert result["artifacts_total"] == 2
    assert result["unpublished_artifacts"] == 1
    assert result["quality_not_passed_artifacts"] == 2
    assert result["invalid_byte_artifacts"] == 1


@pytest.mark.parametrize("invalid", [None, 0, -1, True, float("nan"), float("inf")])
def test_invalid_byte_values_are_counted_and_cannot_produce_ratios(invalid):
    result = observe(artifacts=[artifact("ours", stored_bytes=invalid)])
    assert result["invalid_byte_artifacts"] == 1
    assert result["samples"] == 0
    assert result["ratio"] is None


def test_available_ratio_uses_total_bytes_including_below_target_artifacts():
    tasks = [task("a", "succeeded"), task("b", "succeeded"), task("later", "running")]
    result = observe(
        tasks,
        [artifact("a"), artifact("b", original_bytes=3000, stored_bytes=3000)],
    )
    assert result["status"] == "available"
    assert result["reason"] is None
    assert result["samples"] == 2
    assert result["ratio"] == 4000 / 3200
    assert result["task_total"] == 3
    assert result["tasks_by_state"] == {"succeeded": 2, "running": 1}
    assert result["artifacts_total"] == 2
    assert result["unpublished_artifacts"] == 0
    assert result["quality_not_passed_artifacts"] == 0
    assert result["invalid_byte_artifacts"] == 0
