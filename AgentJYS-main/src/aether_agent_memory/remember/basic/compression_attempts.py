"""Durable compression outputs; extra model review is disabled.

Historical verdicts and ambiguous calls remain readable and are never promoted
to a successful review merely because the current policy removed that stage.
"""

from typing import Any

from aether_agent_memory.runtime.foundation.common import fingerprint

from .compression import CompressionOutput, QualityEvidence


def quality_passed(quality: QualityEvidence) -> bool:
    return quality.passed and not quality.critical_failures and not quality.critical_unknowns


def _result(row: dict[str, Any]) -> dict[str, Any]:
    output = CompressionOutput.model_validate(row["output"])
    quality = row.get("quality")
    if quality is not None:
        evidence = QualityEvidence.model_validate(quality)
        status = "passed" if quality_passed(evidence) else "failed"
    else:
        status = (
            "unknown" if row["review_state"] in {"started", "unknown", "completed"}
            else "not_checked"
        )
    reserved_attempts = row.get(
        "review_attempts", int(row["review_state"] in {"started", "completed", "unknown"})
    )
    return {
        "text": output.text,
        "strategy": output.strategy,
        "quality": quality,
        "quality_status": status,
        "sampling": row.get("sampling"),
        "review_enabled": False,
        "review_reason": "additional_quality_review_disabled",
        # For unknown outcomes this is a reserved invocation, not proof of receipt.
        "attempts": row.get("legacy_attempts", reserved_attempts),
        "review_state": row["review_state"],
        "review_protocol": row.get("review_protocol", "at_most_once_v1"),
    }


def reserve_legacy_reviews(owner: Any, task: Any, part_keys: list[str]) -> None:
    """Retain a task-level fence for historical reviews during output reuse.

    There are no new review calls. Historical unknown outcomes must still prevent
    publication, including when old part keys cannot be recovered from a binding.
    """
    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        if tx.read("remember_compression_review_slots", task.task_id) is not None:
            return
        frozen_binding = tx.read("remember_task_binding", task.task_id)
        if frozen_binding is not None and frozen_binding != owner.checkpoint_binding():
            # Older checkpoints may predate task-indexed sampling and use keys
            # we cannot reconstruct from today's policy/provider configuration.
            # Normal workers reject this mismatch earlier; direct recovery must
            # also fail closed rather than discard a historical unknown outcome.
            tx.write(
                "remember_compression_review_slots", task.task_id,
                {"part_key": "legacy_binding_unknown", "review_state": "unknown"},
            )
            return
        consumed_key = None
        # Old per-block decisions may have a different policy binding. Once an
        # old selected block could have run, preserve its possibly unknown outcome.
        for _, sample in tx.rows("remember_quality_samples"):
            if (
                sample.get("task_id") == task.task_id
                and sample.get("purpose") == "compression"
                and sample.get("selected")
            ):
                consumed_key = sample.get("key", "legacy_selection")
                break
        for key in part_keys:
            if consumed_key is not None:
                break
            checkpoint = tx.read("remember_compression_parts", key)
            if checkpoint is not None:
                if (
                    checkpoint.get("quality") is not None
                    or checkpoint.get("quality_status") not in {"not_sampled", "not_checked"}
                ):
                    consumed_key = key
                continue
            once = tx.read(
                "remember_compression_once",
                fingerprint([task.task_id, key, "compression_review_once_v1"]),
            )
            if once and once.get("review_state") in {"started", "completed", "unknown"}:
                consumed_key = key
                break
            for attempt in range(3):
                legacy = tx.read(
                    "remember_compression_attempts",
                    fingerprint([key, "quality_repair_v1", attempt]),
                )
                if legacy and "output" in legacy:
                    consumed_key = key
                    break
        if consumed_key is not None:
            tx.write(
                "remember_compression_review_slots", task.task_id,
                {"part_key": consumed_key, "review_state": "legacy_consumed_or_unknown"},
            )


async def compress_part(
    owner: Any, ctx: Any, task: Any, text: str, key: str,
) -> dict[str, Any]:
    """Reuse confirmed output or generate it; never invoke quality/repair models."""
    once_key = fingerprint([task.task_id, key, "compression_review_once_v1"])
    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        row = tx.read("remember_compression_once", once_key)
        if row is None:
            # Old output with no verdict may already have an in-flight review.
            # Preserve actual historical attempt counts but never add a new call.
            for attempt in reversed(range(3)):
                legacy = tx.read(
                    "remember_compression_attempts",
                    fingerprint([key, "quality_repair_v1", attempt]),
                )
                if legacy and "output" in legacy:
                    row = {
                        "output": legacy["output"],
                        "quality": legacy.get("quality"),
                        "review_state": "completed" if legacy.get("quality") is not None
                        else "unknown",
                        "review_protocol": "legacy_reused",
                        "legacy_attempts": legacy.get("attempt", attempt + 1),
                    }
                    tx.write("remember_compression_once", once_key, row)
                    break
    if row is not None:
        return _result(row)
    if row is None:
        owner.consume_call(task)
        output = CompressionOutput.model_validate(await owner.compressor.compress(ctx, text))
        with owner.uow.transaction() as tx:
            owner.tasks.guard(tx, task)
            # A concurrent worker may have persisted output/reserved review. Never
            # overwrite its marker with this generation result.
            row = tx.read("remember_compression_once", once_key)
            if row is None:
                row = {
                    "output": output.model_dump(mode="json"),
                    "review_state": "disabled",
                    "review_attempts": 0,
                    "review_protocol": "disabled_v1",
                }
                tx.write("remember_compression_once", once_key, row)
    return _result(row)
