"""Bounded quality repair with durable model outputs and verifier checkpoints."""

from typing import Any

from aether_agent_memory.runtime.foundation.common import fingerprint

from .compression import CompressionOutput, QualityEvidence


def quality_passed(quality: QualityEvidence) -> bool:
    return quality.passed and not quality.critical_failures and not quality.critical_unknowns


async def compress_part(owner: Any, ctx: Any, task: Any, text: str, key: str) -> dict[str, Any]:
    """At most three quality attempts; transport errors propagate without fake success.

    A durable output can be reverified after interruption without generating a
    different artifact. Failed quality attempts remain audit evidence. Providers
    without a repair capability retain the previous single-attempt contract.
    """
    previous = None
    quality = None
    limit = 3 if callable(getattr(owner.compressor, "repair", None)) else 1
    for attempt in range(limit):
        attempt_key = fingerprint([key, "quality_repair_v1", attempt])
        with owner.uow.transaction() as tx:
            owner.tasks.guard(tx, task)
            row = tx.read("remember_compression_attempts", attempt_key) or {}
        if "output" in row:
            output = CompressionOutput.model_validate(row["output"])
        else:
            owner.consume_call(task)
            output = (
                await owner.compressor.compress(ctx, text)
                if previous is None
                else await owner.compressor.repair(ctx, text, previous, quality)
            )
            output = CompressionOutput.model_validate(output)
            row = {"attempt": attempt + 1, "output": output.model_dump(mode="json")}
            with owner.uow.transaction() as tx:
                owner.tasks.guard(tx, task)
                tx.write("remember_compression_attempts", attempt_key, row)
        if "quality" in row:
            quality = QualityEvidence.model_validate(row["quality"])
        else:
            owner.consume_call(task)
            quality = QualityEvidence.model_validate(
                await owner.quality.verify(ctx, text, output.text)
            )
            row = {**row, "quality": quality.model_dump(mode="json")}
            with owner.uow.transaction() as tx:
                owner.tasks.guard(tx, task)
                tx.write("remember_compression_attempts", attempt_key, row)
        previous = output
        if quality_passed(quality):
            break
    assert previous is not None and quality is not None
    return {
        "text": previous.text,
        "strategy": previous.strategy,
        "quality": quality.model_dump(mode="json"),
        "attempts": attempt + 1,
    }
