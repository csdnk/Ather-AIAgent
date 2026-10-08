"""Extra review is disabled; historical verdicts stay truthful.

Regression cases are prepared for the next validation iteration, not executed.
"""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aether_agent_memory.remember.basic.compression import CompressionOutput, QualityEvidence
from aether_agent_memory.remember.basic.compression_attempts import (
    compress_part,
    reserve_legacy_reviews,
)
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.runtime.foundation.common import fingerprint


class TableRows(dict):
    def __call__(self, table):
        return [(key, deepcopy(value)) for (name, key), value in self.items() if name == table]


class Records:
    def __init__(self):
        self.rows = TableRows()

    @contextmanager
    def transaction(self):
        yield self

    def read(self, table, key):
        return deepcopy(self.rows.get((table, key)))

    def write(self, table, key, value):
        self.rows[table, key] = deepcopy(value)


def owner():
    return SimpleNamespace(
        uow=Records(),
        tasks=SimpleNamespace(guard=lambda tx, task: None),
        checkpoint_binding=lambda: "frozen-binding",
        # Historical rate=1 must not restore the removed model stage.
        policy=RememberPolicy(compression_quality_sample_rate=1, memory_support_sample_rate=1),
        consume_call=lambda task: None,
        quality=SimpleNamespace(
            verify=AsyncMock(side_effect=AssertionError("extra review called"))
        ),
    )


@pytest.mark.parametrize("field", ["compression_quality_sample_rate", "memory_support_sample_rate"])
@pytest.mark.parametrize("rate", [-0.01, 1.01, float("nan"), float("inf")])
def test_legacy_policy_fields_remain_validated(field, rate):
    with pytest.raises(ValueError):
        RememberPolicy(**{field: rate})


def test_compression_and_replay_never_review_repair_or_draw_samples():
    subject, task, calls = owner(), SimpleNamespace(task_id="task-1"), []

    class Compressor:
        async def compress(self, ctx, text):
            calls.append(text)
            return CompressionOutput(text="Alice deploys.", strategy="controlled")

        async def repair(self, *args):
            raise AssertionError("removed quality repair called")

    subject.compressor = Compressor()
    first = asyncio.run(compress_part(subject, None, task, "original", "part"))
    replay = asyncio.run(compress_part(subject, None, task, "original", "part"))
    assert first == replay and calls == ["original"]
    assert first["quality_status"] == "not_checked" and first["quality"] is None
    assert first["review_reason"] == "additional_quality_review_disabled"
    assert first["attempts"] == 0 and first["review_enabled"] is False
    subject.quality.verify.assert_not_awaited()
    assert not any(
        table in {"remember_quality_samples", "remember_compression_review_plans"}
        for table, _ in subject.uow.rows
    )


@pytest.mark.parametrize(
    "state,status",
    [
        ("not_started", "not_checked"),
        ("not_selected", "not_checked"),
        ("started", "unknown"),
        ("unknown", "unknown"),
    ],
)
def test_saved_output_preserves_unknown_without_reissuing_review(state, status):
    subject, task = owner(), SimpleNamespace(task_id="task-1")
    subject.uow.write(
        "remember_compression_once",
        fingerprint(
            [
                task.task_id,
                "part",
                "compression_review_once_v1",
            ]
        ),
        {"output": {"text": "fact", "strategy": "old"}, "review_state": state},
    )
    subject.compressor = SimpleNamespace(
        compress=AsyncMock(side_effect=AssertionError("regenerated"))
    )
    result = asyncio.run(compress_part(subject, None, task, "original", "part"))
    assert result["quality_status"] == status and result["quality"] is None
    subject.quality.verify.assert_not_awaited()
    subject.compressor.compress.assert_not_awaited()


@pytest.mark.parametrize("saved_verdict", [True, False, None])
def test_legacy_verdict_is_preserved_without_model_calls(saved_verdict):
    subject, task = owner(), SimpleNamespace(task_id="task-1")
    row = {"attempt": 2, "output": {"text": "fact", "strategy": "old"}}
    if saved_verdict is not None:
        row["quality"] = QualityEvidence(
            passed=saved_verdict,
            policy="old",
            reason="historical verdict",
            retained_fact_fraction=1 if saved_verdict else 0,
        ).model_dump(mode="json")
    subject.uow.write(
        "remember_compression_attempts",
        fingerprint(
            [
                "part",
                "quality_repair_v1",
                1,
            ]
        ),
        row,
    )
    subject.compressor = SimpleNamespace()
    result = asyncio.run(compress_part(subject, None, task, "original", "part"))
    assert result["quality_status"] == (
        "unknown" if saved_verdict is None else "passed" if saved_verdict else "failed"
    )
    assert result["attempts"] == 2
    subject.quality.verify.assert_not_awaited()


def test_unknown_old_binding_remains_blocked():
    subject, task = owner(), SimpleNamespace(task_id="task-1")
    subject.uow.write("remember_task_binding", task.task_id, "historical-binding")
    reserve_legacy_reviews(subject, task, ["new-part-key"])
    assert (
        subject.uow.read("remember_compression_review_slots", task.task_id)["review_state"]
        == "unknown"
    )


@pytest.mark.parametrize("historical", [None, "unknown", "failed", "passed"])
def test_pipeline_publishes_unreviewed_views_without_promoting_old_failures(historical):
    from aether_agent_memory.remember.basic.pipeline import RememberPipeline

    subject, task = owner(), SimpleNamespace(task_id="task-1")
    subject.policy = subject.policy.model_copy(update={"extraction_chunk_tokens": 16})
    subject.tasks.progress = SimpleNamespace(part=lambda *args, **kwargs: None)
    subject.tokenizer = SimpleNamespace(count=len)
    subject.bodies = SimpleNamespace(
        location=lambda *args: SimpleNamespace(
            model_copy=lambda **kwargs: SimpleNamespace(
                model_dump=lambda **kwargs: {"kind": "artifact"}
            )
        )
    )
    generated = []

    class Compressor:
        async def compress(self, ctx, text):
            generated.append(text)
            return CompressionOutput(text=text, strategy="verbatim")

    subject.compressor = Compressor()
    if historical == "unknown":
        subject.uow.write(
            "remember_compression_review_slots",
            task.task_id,
            {
                "part_key": "old-part",
                "review_state": "unknown",
            },
        )
    source = "A" * 16 + "B" * 16 + "C" * 16
    if historical in {"failed", "passed"}:
        from aether_agent_memory.runtime.foundation.requests import text_hash

        key = fingerprint(
            [
                task.task_id,
                subject.checkpoint_binding(),
                "source-hash",
                0,
                text_hash("A" * 16),
                "quality_repair_v1",
            ]
        )
        quality = QualityEvidence(
            passed=historical == "passed",
            policy="old",
            reason="old result",
            retained_fact_fraction=1 if historical == "passed" else 0,
        ).model_dump(mode="json")
        subject.uow.write(
            "remember_compression_parts",
            key,
            {
                "text": "A" * 16,
                "strategy": "old",
                "quality": quality,
                "quality_status": historical,
                "start_char": 0,
                "end_char": 16,
                "attempts": 1,
            },
        )
    item = SimpleNamespace(
        content=source, content_hash="source-hash", ref=SimpleNamespace(scope="scope")
    )
    first = asyncio.run(RememberPipeline.generate_compression(subject, None, task, item))
    replay = asyncio.run(RememberPipeline.generate_compression(subject, None, task, item))
    result = first["result"]
    assert first == replay
    assert result["published"] is (historical in {None, "passed"})
    assert result["quality"] == (
        "not_sampled"
        if historical is None
        else "sampled_passed"
        if historical == "passed"
        else "failed"
    )
    assert result["quality_review_enabled"] is False
    assert result["quality_review_reason"] == "additional_quality_review_disabled"
    assert result["declared_use"] == "extraction_view"
    assert generated == (
        ["B" * 16, "C" * 16]
        if historical in {"failed", "passed"}
        else ["A" * 16, "B" * 16, "C" * 16]
    )
    subject.quality.verify.assert_not_awaited()
    assert not any(
        table in {"remember_quality_samples", "remember_compression_review_plans"}
        for table, _ in subject.uow.rows
    )
