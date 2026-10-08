"""Safe aggregate views of compression receipts and deployment-bound placement actions."""

import math
from collections.abc import Collection
from typing import Any, Protocol, TypeGuard


class ObservationReader(Protocol):
    def rows(self, table: str) -> list[tuple[str, Any]]: ...
    def read(self, table: str, key: str) -> Any: ...


def positive(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (math.isfinite(value) and value > 0)
    )


def placement_item(
    task_id: str, row: dict[str, Any], trigger: dict[str, Any] | None
) -> dict[str, Any]:
    """Expose bounded evidence, never arbitrary provider messages or memory content."""
    import re
    from contextlib import suppress
    from datetime import datetime

    from pydantic import ValidationError

    from aether_agent_memory.operate.contracts.models import ActionRecord

    intent = row.get("intent", {})
    decision = intent.get("decision", {})
    feedback = row.get("feedback") or {}
    memory = decision.get("memory", {})
    scope = memory.get("scope", {})
    state = row.get("state")
    outcomes: dict[str | None, str] = {
        "generated": "pending",
        "submitted": "running",
        "unknown": "unconfirmed",
        "failed": "failed",
        "cancelled": "cancelled",
    }
    result = outcomes.get(state, "unconfirmed")
    if state == "succeeded":
        try:
            ActionRecord.model_validate(row)
        except ValidationError:
            pass
        else:
            result = "succeeded" if intent.get("provider_mode") == "real" else "simulated"
    reasons = {
        "successful read creates hot replica": "create_hot_replica",
        "no reads; remove hot replica": "remove_hot_replica",
        "new durable memory enters warm cache": "new_memory",
        "observed successful read promotes warm copy": "successful_read",
        "no successful reads in current version": "no_reads",
    }
    reason = decision.get("reason", "")
    heat_match = re.fullmatch(
        r"heat=([0-9]+\.[0-9]{6}); desired=(cold|warm|hot); "
        r"(?:hysteresis_v1|two_tier_hysteresis_v2)",
        reason,
    )
    duration = None
    if result in {"succeeded", "simulated", "failed", "cancelled"}:
        with suppress(KeyError, TypeError, ValueError):
            elapsed = (
                datetime.fromisoformat(feedback["observed_at"])
                - datetime.fromisoformat(intent["created_at"])
            ).total_seconds()
            duration = elapsed if elapsed >= 0 else None
    kind = (trigger or {}).get("kind")
    return {
        "action_id": intent.get("action_id"),
        "task_id": task_id,
        "memory_id": memory.get("memory_id"),
        "memory_version": memory.get("version"),
        "tenant_id": scope.get("tenant_id"),
        "user_id": scope.get("user_id"),
        "created_at": intent.get("created_at"),
        "observed_at": feedback.get("observed_at"),
        "duration_seconds": duration,
        "trigger": kind
        if kind
        in {
            "memory.changed",
            "recall.access",
            "periodic",
            "new_input",
            "threshold_crossing",
            "upgrade",
            "capability_change",
            "pending_completion",
            "temporary_failure",
            "placement_unconfirmed",
            "due",
        }
        else "unrecorded",
        "decision_reason": "heat_policy" if heat_match else reasons.get(reason, "unrecorded"),
        "heat": float(heat_match[1]) if heat_match else None,
        "desired_tier": heat_match[2] if heat_match else None,
        "current_tier": decision.get("current_tier"),
        "target_tier": decision.get("target_tier"),
        "outcome": decision.get("outcome"),
        "policy_version": decision.get("policy_version"),
        "state": state,
        "result": result,
        "provider_mode": intent.get("provider_mode"),
        "cleanup_state": row.get("cleanup_state"),
        "feedback_state": feedback.get("state"),
        "verified_tier": decision.get("target_tier")
        if result in {"succeeded", "simulated"}
        else None,
        "read_verified": result in {"succeeded", "simulated"},
    }


def memory_observations(
    tx: ObservationReader, task_ids: Collection[str], observed_at: str
) -> dict[str, Any]:
    original: int | float = 0
    stored: int | float = 0
    samples = 0
    compression_states: dict[str, int] = {}
    for task_id, envelope in tx.rows("tasks"):
        if task_id not in task_ids:
            continue
        record = envelope.get("record", {})
        if record.get("kind") != "remember.compress":
            continue
        state = record.get("state") or "unknown"
        compression_states[state] = compression_states.get(state, 0) + 1
    artifact_counts = {
        "artifacts_total": 0,
        "unpublished_artifacts": 0,
        "quality_not_passed_artifacts": 0,
        "invalid_byte_artifacts": 0,
    }
    for _, row in tx.rows("remember_artifacts"):
        if row.get("task_id") not in task_ids:
            continue
        before, after = row.get("original_bytes"), row.get("stored_bytes")
        published = row.get("published") is True
        quality_passed = row.get("quality") == "passed"
        valid_bytes = positive(before) and positive(after)
        # These independent counts may overlap; they are not exclusive buckets.
        artifact_counts["artifacts_total"] += 1
        artifact_counts["unpublished_artifacts"] += int(not published)
        artifact_counts["quality_not_passed_artifacts"] += int(not quality_passed)
        artifact_counts["invalid_byte_artifacts"] += int(not valid_bytes)
        if published and quality_passed and valid_bytes:
            original += before
            stored += after
            samples += 1
    compression_reason = None
    if not samples:
        if any(
            compression_states.get(state)
            for state in ("pending", "running", "retry_wait", "recovery_wait")
        ):
            compression_reason = "pending"
        elif compression_states.get("failed"):
            compression_reason = "failed"
        elif not compression_states and not artifact_counts["artifacts_total"]:
            compression_reason = "not_triggered"
        else:
            compression_reason = "no_published_artifacts"
    actions: dict[str, dict[str, Any]] = {}
    for task_id, intent in tx.rows("operate_task_actions"):
        if task_id not in task_ids or intent.get("action_id") in actions:
            continue
        row = tx.read("operate_actions", intent.get("action_id"))
        if not row:
            continue
        detail = row.get("intent", {})
        decision = detail.get("decision", {})
        actions[intent["action_id"]] = {
            "task_id": task_id,
            "state": row.get("state"),
            "created_at": detail.get("created_at"),
            "outcome": decision.get("outcome"),
            "policy_version": decision.get("policy_version"),
            "current_tier": decision.get("current_tier"),
            "target_tier": decision.get("target_tier"),
            "provider_mode": detail.get("provider_mode"),
            "feedback_state": (row.get("feedback") or {}).get("state"),
        }
    states: dict[str, int] = {}
    for row in actions.values():
        state = row["state"] or "unknown"
        states[state] = states.get(state, 0) + 1
    ordered = sorted(actions.values(), key=lambda row: row["created_at"] or "", reverse=True)
    return {
        "observed_at": observed_at,
        "compression": {
            "status": "available" if samples else "no_samples",
            "reason": compression_reason,
            "task_total": sum(compression_states.values()),
            "tasks_by_state": compression_states,
            **artifact_counts,
            "samples": samples,
            "original_bytes": original if samples else None,
            "stored_bytes": stored if samples else None,
            "ratio": original / stored if stored else None,
            "scope": "published text artifacts from deployment-bound tasks; "
            "historical versions included; not total physical storage",
        },
        "placement": {
            "status": "available",
            "total": len(actions),
            "by_state": states,
            "items": ordered[:30],
            "truncated": len(ordered) > 30,
            "scope": "deployment-bound recorded actions; not all memory placement",
        },
    }
