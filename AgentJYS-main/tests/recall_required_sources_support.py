"""AET-41 strict-policy input and transactional access-event observations.

The event witness forwards Events.append and mirrors metadata in the same
transaction. Only its own test namespace is read, never product outbox tables.
An append attempt is distinguished from a committed event, including rollback.
"""

import json
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.contracts.models import AccessObserved, RecallRecord
from aether_agent_memory.runtime.contracts.models import TaskOperationView
from aether_agent_memory.runtime.foundation.transactions import native


def strict_policy(path):
    """Single independent server profile; no allow-policy profile is required.

    P3_RECALL_STRICT_POLICY_FILE contains settings, policy_version,
    decision_reference, partial_delivery='forbid', required_sources=['working','long_term'].
    Policy metadata is not inserted into client requests or guessed server fields.
    """
    if not path:
        return {}, {
            "confirmed": False,
            "origin": "current_server_deployment",
            "restriction": "AET-41: approved server strict-policy configuration unavailable; "
            "strict capability must be implemented by the product",
        }
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    assert set(value) == {
        "settings",
        "policy_version",
        "decision_reference",
        "partial_delivery",
        "required_sources",
    }
    assert value["partial_delivery"] == "forbid"
    assert value["required_sources"] == ["working", "long_term"]
    assert all(
        isinstance(value[key], str) and value[key].strip()
        for key in ("policy_version", "decision_reference")
    )
    assert isinstance(value["settings"], dict)
    RecallSettings.model_validate(value["settings"])
    return value["settings"], {
        "confirmed": True,
        "origin": str(Path(path).resolve()),
        "expected_policy_version": value["policy_version"],
        "decision_reference": value["decision_reference"],
        "partial_delivery": "forbid",
        "required_sources": value["required_sources"],
        "manifest_sha256": sha256(raw).hexdigest(),
    }


class AccessEventWitness:
    def __init__(self, runtime, monkeypatch):
        self.uow = runtime.foundation.uow
        self.namespace = "test_aet41_events_" + uuid4().hex
        self.attempts = []
        events = runtime.foundation.events
        append = events.append

        def observed(tx, ctx, event):
            if event.event_type != "recall.access":
                return append(tx, ctx, event)
            access = AccessObserved.model_validate(event.payload)
            row = {
                "operation_id": ctx.operation_id,
                "event_id": event.event_id,
                "trace_id": event.trace_id,
                "recall_id": access.recall_id,
                "memory": access.memory.model_dump(mode="json"),
                "stage": access.stage,
                "outcome": access.outcome,
            }
            self.attempts.append(row)
            result = append(tx, ctx, event)
            native(tx).write(self.namespace, event.event_id, row)
            return result

        monkeypatch.setattr(events, "append", observed)

    def observe(self, operation_id):
        with self.uow.transaction() as tx:
            committed = [
                row for _, row in tx.rows(self.namespace) if row["operation_id"] == operation_id
            ]
        attempts = [row for row in self.attempts if row["operation_id"] == operation_id]
        return {
            "append_attempts": attempts,
            "committed_events": committed,
            "packed_attempt_count": sum(
                row["stage"] == "packed" and row["outcome"] == "succeeded" for row in attempts
            ),
            "packed_committed_count": sum(
                row["stage"] == "packed" and row["outcome"] == "succeeded" for row in committed
            ),
        }


def assert_failed_consistently(task_payload, record_payload, events, job_id):
    task = TaskOperationView.model_validate(task_payload)
    record = RecallRecord.model_validate(record_payload)
    assert task.task_id == job_id and task.kind == "recall.execute"
    assert task.state == "failed", "strict request produced a successful/nonterminal job"
    assert task.error_code is not None and task.error_code.value == "DEPENDENCY_UNAVAILABLE"
    assert task.result_ref is None, "failed strict request retained a successful result reference"
    assert task.subject.object_id == record.recall_id
    assert record.state == "failed" and not record.result_available
    assert events["packed_attempt_count"] == events["packed_committed_count"] == 0
    for row in (*events["append_attempts"], *events["committed_events"]):
        assert row["recall_id"] == record.recall_id
        assert not (row["stage"] in {"packed", "delivered"} and row["outcome"] == "succeeded")
    return record
