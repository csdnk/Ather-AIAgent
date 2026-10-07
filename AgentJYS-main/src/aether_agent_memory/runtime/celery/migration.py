"""Non-destructive binding audit and repeatable routing-marker migration."""

from typing import Any

from aether_agent_memory.runtime.temporal.config import deployment_configuration


def inspect_bindings(uow: Any, config: Any, *, apply: bool = False) -> dict[str, Any]:
    isolated = deployment_configuration(config.temporal)
    temporal = {
        "backend": "temporal",
        "deployment_id": isolated.deployment_id,
        "namespace": isolated.namespace,
        "task_queue_prefix": isolated.task_queue_prefix,
    }
    expected = {
        "backend": "hybrid_v1",
        "temporal": temporal,
        "remember": "celery",
        "queue_prefix": config.celery.queue_prefix,
    }
    counts = {"temporal": 0, "celery": 0}
    with uow.transaction() as tx:
        prior = tx.read("meta", "execution_backend")
        if prior not in (None, temporal, expected):
            raise ValueError("deployment execution identity changed")
        for key, row in tx.active_task_rows(include_attention=True):
            binding = tx.read("temporal_bindings", key)
            if binding is None:
                raise ValueError("active task has no original execution binding")
            job = binding["job"]
            backend = binding.get("backend", "temporal")
            prefix = "celery" if backend == "celery" else "p3"
            if (
                backend not in counts
                or job["job_id"] != key
                or job["deployment_id"] != isolated.deployment_id
                or job["input_hash"] != row["record"]["input_hash"]
                or job["kind"] != row["record"]["kind"]
                or job["plan_version"] != "1"
                or binding["namespace"] != isolated.namespace
                or binding["workflow_id"]
                != f"{prefix}/{isolated.deployment_id}/{job['kind']}/{key}"
            ):
                raise ValueError("active execution binding does not match immutable task")
            if backend == "celery" and tx.read("celery_jobs", key) is None:
                raise ValueError("active Celery task has no durable execution state")
            counts[backend] += 1
        for key, _ in tx.pending_delivery_rows():
            if tx.read("temporal_bindings", key) is None:
                raise ValueError("pending event delivery has no original execution binding")
        for key, row in tx.rows("recall_requests"):
            if row["record"]["state"] in {"accepted", "running"}:
                binding = tx.read("temporal_bindings", key)
                if binding is None or binding.get("backend", "temporal") != "temporal":
                    raise ValueError("active Recall request has no original Temporal binding")
        if apply:
            tx.write("meta", "execution_backend", expected)
    return {
        "valid": True,
        "applied": apply,
        "active_bindings": counts,
        "previous_backend": prior.get("backend") if prior else None,
        "target": "hybrid_v1",
    }
