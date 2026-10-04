"""Bounded, read-only metadata collection; no arbitrary upstream bodies reach Web."""

import time
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from pydantic import RootModel

from aether_agent_memory.runtime.contracts.models import RecordRef, WorkflowBinding
from aether_p4_simulator.validation.errors import ValidationError

from .coverage import Coverage
from .models import Diagnostics

if TYPE_CHECKING:
    from .execution import StoryRun

type Metadata = dict[str, Any]


def collect(run: "StoryRun", coverage: Coverage) -> Diagnostics:
    result = Diagnostics(state="complete")
    deadline = time.monotonic() + 12

    def read[T](
        path: str, call: Callable[..., RootModel[T]], validate: Callable[[T], object]
    ) -> T | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result.state = "incomplete"
            return None
        try:
            value = call(timeout_seconds=min(2, remaining)).root
            validate(value)
            coverage.mark("GET", path, "passed")
            return value
        except Exception as error:
            # Diagnostics must never replace a story failure or forward exception text.
            result.state = "incomplete"
            if isinstance(error, ValidationError) and error.status in {403, 404, 503}:
                coverage.mark("GET", path, "blocked")
            result.notes.append(path + "：未能完成有界诊断；未影响或重放业务操作")
            return None

    def items(value: Metadata, key: str = "items", identity: str | None = None) -> list[Metadata]:
        rows = value.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("invalid metadata list")
        cursor = value.get("next_cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise ValueError("invalid pagination token")
        before = value.get("next_before")
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError("invalid trace pagination sequence")
        for row in rows:
            if identity and not isinstance(row.get(identity), str):
                raise ValueError("invalid metadata identity")
            for field in ("task_id", "trace_id"):
                if row.get(field) is not None and not isinstance(row[field], str):
                    raise ValueError("invalid related identity")
        return rows

    def owned(raw: object) -> bool:
        ref = RecordRef.model_validate(raw)
        return run.scope is not None and run.scope_matches(ref.scope)

    trace_ids: set[str] = set()
    found_tasks: set[str] = set()
    cursor: str | None = None
    for _ in range(3):
        page = read(
            "/p3/tasks",
            partial(run.client.tasks, cursor),
            lambda value: items(value, identity="task_id"),
        )
        if page is None:
            break
        for row in page["items"]:
            if row.get("task_id") in run.task_ids:
                try:
                    matches = owned(row.get("subject"))
                except ValueError:
                    matches = False
                if not matches:
                    result.state = "incomplete"
                    continue
                found_tasks.add(row["task_id"])
                trace_id = row.get("trace_id")
                if isinstance(trace_id, str) and trace_id:
                    trace_ids.add(trace_id)
        cursor = page.get("next_cursor")
        if not cursor or found_tasks >= run.task_ids:
            break
    result.task_count = len(found_tasks)
    if found_tasks != run.task_ids:
        result.state = "incomplete"
        result.notes.append("有限目录窗口未覆盖本轮全部任务，计数不是全量审计。")

    seen_traces: set[str] = set()
    before: int | None = None
    for _ in range(3):
        page = read(
            "/p3/traces",
            partial(run.client.traces, before),
            lambda value: items(value, identity="trace_id"),
        )
        if page is None:
            break
        seen_traces.update(
            row["trace_id"] for row in page["items"] if row.get("trace_id") in trace_ids
        )
        before = page.get("next_before")
        if not before or seen_traces >= trace_ids:
            break
    result.trace_count = len(seen_traces)
    if not trace_ids or seen_traces != trace_ids:
        result.state = "incomplete"
        result.notes.append("本轮关联轨迹在保留窗口中不完整；未把其他运行的轨迹计入。")
    for trace_id in sorted(trace_ids)[:3]:
        page = read(
            "/p3/logs/{trace_id}",
            partial(run.client.logs, trace_id),
            lambda value: items(value, "records"),
        )
        if page is not None:
            result.log_record_count += sum(
                row.get("trace_id") == trace_id and row.get("task_id") in found_tasks
                for row in page["records"]
            )
    if len(trace_ids) > 3:
        result.state = "incomplete"
        result.notes.append("日志仅抽样最多 3 条已关联轨迹；不展示正文、异常原文或其他任务记录。")
    incidents = read(
        "/p3/incidents",
        run.client.incidents,
        lambda value: items({"items": value}),
    )
    if incidents is not None:
        for row in incidents:
            try:
                if owned(row.get("subject")):
                    result.incident_count += 1
            except ValueError:
                result.state = "incomplete"

    def check_field(value: Metadata, key: str, choices: set[str]) -> None:
        if value.get(key) not in choices:
            raise ValueError("unexpected status")

    read(
        "/p3/runtime",
        run.client.runtime,
        lambda value: check_field(value, "state", {"observed", "attention_required"}),
    )
    read("/p3/readyz", run.client.readyz, lambda value: check_field(value, "readiness", {"ready"}))

    def check_periodic(value: Metadata) -> None:
        WorkflowBinding.model_validate(value.get("binding"))
        if type(value.get("revision")) is not int or value["revision"] < 1:
            raise ValueError("invalid periodic revision")

    # This operator-only endpoint returns a binding, not desired_state. A 403 is
    # an explicit coverage gap; never change permissions or scheduling for a demo.
    read("/p3/periodic/control", run.client.periodic_status, check_periodic)
    if result.state == "complete":
        result.notes.append("只读元数据已采集；不以日志计数代替业务结果。")
    return result
