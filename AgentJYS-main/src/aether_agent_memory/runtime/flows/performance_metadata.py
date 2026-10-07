"""Set-based dashboard metadata reads on the independent telemetry connection."""

from __future__ import annotations

import json
from typing import Any

from aether_agent_memory.runtime.contracts.models import TaskState
from aether_agent_memory.runtime.flows.dashboard import memory_observations


class _Snapshot:
    def __init__(self, tasks: list[tuple[str, Any]], artifacts: list[tuple[str, Any]]) -> None:
        self.tables = {"tasks": tasks, "remember_artifacts": artifacts, "operate_task_actions": []}

    def rows(self, table: str) -> list[tuple[str, Any]]:
        return self.tables.get(table, [])

    def read(self, table: str, key: str) -> None:
        return None


def _queries(postgres: bool) -> tuple[str, str, str]:
    marker = "%s" if postgres else "?"

    def field(alias: str, *path: str, raw: bool = False) -> str:
        if postgres:
            operator = "#>" if raw else "#>>"
            return f"{alias}.value::jsonb {operator} '{{{','.join(path)}}}'"
        document = f"(CASE WHEN json_valid({alias}.value) THEN {alias}.value ELSE '{{}}' END)"
        pointer = "$." + ".".join(path)
        return (
            f"json({document} -> '{pointer}')" if raw else (f"json_extract({document},'{pointer}')")
        )

    def obj(*pairs: tuple[str, str]) -> str:
        function = "jsonb_build_object" if postgres else "json_object"
        return function + "(" + ",".join(f"'{key}',{value}" for key, value in pairs) + ")"

    def numeric(alias: str, name: str) -> str:
        value = field(alias, name, raw=True)
        type_test = (
            f"jsonb_typeof({value})='number'"
            if postgres
            else f"json_type({value}) IN ('integer','real')"
        )
        return f"CASE WHEN {type_test} THEN {value} END"

    def typed(alias: str, *path: str, kind: str) -> str:
        raw = field(alias, *path, raw=True)
        if postgres:
            type_test = f"jsonb_typeof({raw})='{kind}'"
        else:
            kinds = "'true','false'" if kind == "boolean" else "'text'"
            type_test = f"json_type({raw}) IN ({kinds})"
        value = raw if kind == "boolean" else field(alias, *path)
        return f"CASE WHEN {type_test} THEN {value} END"

    memory_id = typed("m", "value", "ref", "memory_id", kind="string")
    working = (
        f"SELECT DISTINCT {memory_id} AS memory_id FROM capability_records m "
        "WHERE m.namespace='p3_rf_records' AND m.tenant='system' "
        f"AND {field('m', 'ref', 'object_type')}='memory' "
        f"AND {field('m', 'value', 'kind')}='working' "
        f"AND length({memory_id}) BETWEEN 1 AND 160"
    )
    binding_filter = (
        "b.namespace='p3_rf_temporal_bindings' AND b.tenant='system' "
        f"AND {field('b', 'namespace')}={marker} "
        f"AND {field('b', 'job', 'deployment_id')}={marker}"
    )
    states = ",".join(f"'{state.value}'" for state in TaskState)
    state = field("t", "record", "state")
    safe_state = f"CASE WHEN {state} IN ({states}) THEN {state} ELSE 'unknown' END"
    record = obj(("kind", "'remember.compress'"), ("state", safe_state))
    tasks = (
        f"SELECT t.key AS key, {obj(('record', record))} AS value "
        "FROM capability_records t JOIN capability_records b ON b.key=t.key "
        f"AND {binding_filter} WHERE t.namespace='p3_rf_tasks' AND t.tenant='system' "
        f"AND {field('t', 'record', 'kind')}='remember.compress'"
    )
    quality = field("a", "quality")
    safe_quality = f"CASE WHEN {quality}='passed' THEN 'passed' ELSE 'unknown' END"
    artifact = obj(
        ("task_id", field("a", "task_id")),
        ("published", typed("a", "published", kind="boolean")),
        ("quality", safe_quality),
        ("original_bytes", numeric("a", "original_bytes")),
        ("stored_bytes", numeric("a", "stored_bytes")),
    )
    artifacts = (
        f"SELECT a.key AS key, {artifact} AS value FROM capability_records a "
        f"JOIN capability_records b ON b.key={field('a', 'task_id')} AND {binding_filter} "
        "WHERE a.namespace='p3_rf_remember_artifacts' AND a.tenant='system'"
    )
    return working, tasks, artifacts


def _row(raw: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return dict(raw) if hasattr(raw, "keys") else dict(zip(names, raw, strict=True))


def _records(raw_rows: list[Any]) -> list[tuple[str, Any]]:
    rows = []
    for raw in raw_rows:
        row = _row(raw, ("key", "value"))
        value = row["value"]
        rows.append((row["key"], json.loads(value) if isinstance(value, str) else value))
    return rows


def performance_metadata(
    telemetry: Any, *, namespace: str, deployment_id: str, observed_at: str
) -> tuple[set[str], dict[str, Any]]:
    """Read three scalar projections without the metadata UoW or per-record calls.

    Telemetry and metadata must share the same database. The caller verifies this
    binding and platform permission. Working memory kind is immutable, so its IDs
    may come from any retained version; compression follows the deployment binding.
    Provider failures propagate for the caller to mark observations unavailable.
    """
    working_sql, tasks_sql, artifacts_sql = _queries(
        getattr(telemetry, "backend", None) == "postgresql"
    )
    with telemetry.reader() as db:
        working_rows = db.execute(working_sql).fetchall()
        tasks = _records(db.execute(tasks_sql, (namespace, deployment_id)).fetchall())
        artifacts = _records(db.execute(artifacts_sql, (namespace, deployment_id)).fetchall())
    working_ids = {
        row["memory_id"]
        for raw in working_rows
        if isinstance((row := _row(raw, ("memory_id",)))["memory_id"], str)
    }
    task_ids = {key for key, _ in tasks} | {value["task_id"] for _, value in artifacts}
    metrics = memory_observations(_Snapshot(tasks, artifacts), task_ids, observed_at)
    return working_ids, metrics
