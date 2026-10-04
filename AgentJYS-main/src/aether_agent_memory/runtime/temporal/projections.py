"""Idempotent business views of terminal task facts; never execute providers."""

from aether_agent_memory.recall.contracts.models import RecallRecord
from aether_agent_memory.runtime.contracts.foundation import IncidentRecord
from aether_agent_memory.runtime.contracts.models import EffectStatus, TaskRecord, TaskState
from aether_agent_memory.runtime.foundation.tasks import Tasks
from aether_agent_memory.runtime.storage.ports import MetadataTransaction


def project_terminal(tasks: Tasks, tx: MetadataTransaction, task: TaskRecord) -> None:
    task_row, _ = tasks.load(tx, task.task_id)
    reason = task_row.get("terminal_reason") or (
        task.error_code.value if task.error_code else task.state.value
    )
    if task.kind == "recall.execute" and task.state != TaskState.SUCCEEDED:
        row = tx.read("recall_requests", task.task_id)
        if row is not None and row["record"]["state"] != "failed":
            record = RecallRecord.model_validate(
                {
                    **row["record"],
                    "state": "failed",
                    "result_available": False,
                    "reason": reason,
                    "revision": row["record"]["revision"] + 1,
                }
            )
            tx.write(
                "recall_requests", task.task_id, {**row, "record": record.model_dump(mode="json")}
            )
    if task.kind == "runtime.event_delivery" and task.state != TaskState.SUCCEEDED:
        row = tx.read("deliveries", task.task_id)
        if row and row["state"] not in {"acknowledged", "attention_required"}:
            tx.write(
                "deliveries",
                task.task_id,
                {
                    **row,
                    "state": "attention_required",
                    "error_code": reason,
                    "revision": row["revision"] + 1,
                    "lease": None,
                },
            )
    for key, row in tx.rows("incidents"):
        if row["task_id"] != task.task_id or row["record"]["state"] in {
            "resolved",
            "attention_required",
        }:
            continue
        incident = IncidentRecord.model_validate(
            {
                **row["record"],
                "state": "attention_required",
                "verification": "unknown"
                if task.effect_status == EffectStatus.UNKNOWN or task.state == TaskState.SUCCEEDED
                else "failed",
                "reason_code": "VERIFICATION_MISSING"
                if task.state == TaskState.SUCCEEDED
                else reason,
                "revision": row["record"]["revision"] + 1,
                "updated_at": tasks.clock(),
            }
        )
        tx.write("incidents", key, {**row, "record": incident.model_dump(mode="json")})
