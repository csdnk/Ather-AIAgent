"""Safe aggregate views of compression receipts and deployment-bound placement actions."""

import math


def positive(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (math.isfinite(value) and value > 0)
    )


def memory_observations(tx, task_ids, observed_at):
    original, stored, samples = 0, 0, 0
    for _, row in tx.rows("remember_artifacts"):
        if row.get("task_id") not in task_ids:
            continue
        before, after = row.get("original_bytes"), row.get("stored_bytes")
        if (
            row.get("published") is True
            and row.get("quality") == "passed"
            and positive(before)
            and positive(after)
        ):
            original += before
            stored += after
            samples += 1
    actions = {}
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
            "current_tier": decision.get("current_tier"),
            "target_tier": decision.get("target_tier"),
            "provider_mode": detail.get("provider_mode"),
            "feedback_state": (row.get("feedback") or {}).get("state"),
        }
    states = {}
    for row in actions.values():
        state = row["state"] or "unknown"
        states[state] = states.get(state, 0) + 1
    ordered = sorted(actions.values(), key=lambda row: row["created_at"] or "", reverse=True)
    return {
        "observed_at": observed_at,
        "compression": {
            "status": "available" if samples else "no_samples",
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
