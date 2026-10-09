"""Dense Working recovery preserves source identity, coverage and checkpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aether_agent_memory.remember.basic import candidate_consolidation as consolidation
from aether_agent_memory.remember.basic.official_langmem import (
    CandidateCapacityError,
    CandidateExtractionResult,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint

from .test_official_langmem import snapshot
from .test_remember_candidate_batching import app_and_input, candidate


def dense_input():
    # Different facts, no repeated padding, and below the long-route byte threshold.
    return snapshot("dense", "\n".join(f"Person {i} selected editor {i}." for i in range(72)))


def ranged_candidates(item, start, end):
    entries = []
    position = start
    for line in item.content[start:end].splitlines(keepends=True):
        text = line.rstrip("\r\n")
        if text:
            entry = candidate(item)
            evidence = entry.candidate.evidence[0].model_copy(
                update={"start_char": position, "end_char": position + len(text), "quote": text}
            )
            fact = entry.candidate.model_copy(update={"text": text, "evidence": (evidence,)})
            entries.append(
                entry.model_copy(
                    update={
                        "candidate": fact,
                        "candidate_id": fingerprint([fact.model_dump(mode="json")]),
                    }
                )
            )
        position += len(line)
    return tuple(entries)


@pytest.mark.parametrize("failure", ["candidate_capacity", "output_capacity", "input_budget"])
async def test_dense_short_recovers_without_losing_facts_or_using_llmlingua(monkeypatch, failure):
    app, _, _ = app_and_input(1)
    item = dense_input()
    app.llmlingua_preprocessor = SimpleNamespace(acompress=AsyncMock())
    task = SimpleNamespace(task_id="dense-task", kind="remember.extract")
    calls = []
    if failure == "input_budget":
        monkeypatch.setattr(
            consolidation,
            "_cost",
            lambda owner, stage, payload: (
                owner.policy.comparison_context_tokens + 1
                if stage == "extraction" and len(payload["messages"][0]["content"]) > 2200
                else 0
            ),
        )

    async def extract(ctx, batch, version, **kwargs):
        assert batch == (item,)
        start, end = kwargs["source_ranges"][item.sources[0].source_id]
        calls.append((start, end))
        entries = ranged_candidates(item, start, end)
        if failure == "output_capacity" and len(entries) > 32:
            raise CandidateCapacityError(len(entries), reason="output_truncated")
        return CandidateExtractionResult(
            candidates=entries, model_id="model", policy_version=version
        )

    app.extraction.extract_candidates.side_effect = extract
    result = await consolidation.prepare_candidate_consolidation(app, None, task, (item,))
    assert result["extracted_candidate_count"] == 72
    assert {entry.candidate.text for entry in result["proposals"]} == set(item.content.splitlines())
    assert result["validation_items"] == (item,)
    assert result["discovery"]["extraction_subdivisions"] > 0
    assert await consolidation.prepare_candidate_consolidation(app, None, task, (item,)) == result
    app.llmlingua_preprocessor.acompress.assert_not_awaited()
    splits = [
        row
        for (table, _), row in app.uow.rows.items()
        if table == "remember_candidate_extraction_splits"
    ]
    assert any(row["reason"] == failure for row in splits)
    assert all(row["strategy"] == "semantic_capacity_v2" for row in splits)
    details = next(row["capacity"] for row in splits if row["reason"] == failure)
    if failure == "candidate_capacity":
        assert details["candidate_count"] > details["candidate_limit"]
    elif failure == "input_budget":
        assert details["input_tokens"] > details["input_limit"]
    else:
        assert details["output_truncated"] is True
        assert details["output_limit"] > 0


async def test_dense_split_resume_reuses_completed_children_and_ignores_legacy_exhaustion():
    app, _, _ = app_and_input(1)
    item = dense_input()
    task = SimpleNamespace(task_id="dense-resume", kind="remember.extract")
    binding = fingerprint(
        [
            "official_candidate_extraction_v1",
            task.task_id,
            task.kind,
            app.checkpoint_binding(),
            "sources-v1",
            {item.sources[0].source_id: (0, len(item.content))},
            0,
            [[item.ref.model_dump(mode="json"), item.content_hash]],
        ]
    )
    app.uow.write("remember_short_extraction_failures", binding, 2)
    app.uow.write("remember_candidate_extraction_splits", binding, {"reason": "output_capacity"})
    calls, completed = [], []
    interrupted = False

    async def extract(ctx, batch, version, **kwargs):
        nonlocal interrupted
        start, end = kwargs["source_ranges"][item.sources[0].source_id]
        calls.append((start, end))
        entries = ranged_candidates(item, start, end)
        if len(entries) > 32:
            raise CandidateCapacityError(len(entries), reason="output_truncated")
        if completed and not interrupted:
            interrupted = True
            raise RuntimeError("worker restart")
        completed.append((start, end))
        return CandidateExtractionResult(
            candidates=entries, model_id="model", policy_version=version
        )

    app.extraction.extract_candidates.side_effect = extract
    with pytest.raises(RuntimeError, match="worker restart"):
        await consolidation.prepare_candidate_consolidation(app, None, task, (item,))
    app.related.assert_not_awaited()
    first_completed = completed[0]
    result = await consolidation.prepare_candidate_consolidation(app, None, task, (item,))
    assert result["extracted_candidate_count"] == 72
    assert calls.count(first_completed) == 1
    assert (0, len(item.content)) not in calls
    assert completed[0][0] == 0 and completed[-1][1] == len(item.content)
    assert all(
        left[1] == right[0] for left, right in zip(completed[:-1], completed[1:], strict=True)
    )


async def test_indivisible_short_sentence_fails_without_cutting_subject_or_unbounded_retries():
    app, _, _ = app_and_input(1)
    item = snapshot("single", "沈舟的首选编辑器是VS Code。")
    task = SimpleNamespace(task_id="single", kind="remember.extract")
    app.extraction.extract_candidates.side_effect = CandidateCapacityError(
        0, reason="output_truncated"
    )
    for _ in range(2):
        with pytest.raises(FoundationError, match="indivisible.*semantic"):
            await consolidation.prepare_candidate_consolidation(app, None, task, (item,))
    assert app.extraction.extract_candidates.await_count == 1
    app.related.assert_not_awaited()
    app.extraction.decide_candidates.assert_not_awaited()


async def test_dense_split_depth_limit_cannot_publish_or_spend_more_calls_on_resume():
    app, _, _ = app_and_input(1)
    app.policy = app.policy.model_copy(update={"extraction_split_depth": 1})
    item = dense_input()
    task = SimpleNamespace(task_id="depth-bound", kind="remember.extract")
    app.extraction.extract_candidates.side_effect = CandidateCapacityError(
        0, reason="output_truncated"
    )
    for _ in range(2):
        with pytest.raises(FoundationError, match="subdivision limit"):
            await consolidation.prepare_candidate_consolidation(app, None, task, (item,))
    assert app.extraction.extract_candidates.await_count == 2
    app.related.assert_not_awaited()
    app.extraction.decide_candidates.assert_not_awaited()


async def test_source_withdrawal_after_child_checkpoint_rejects_resume():
    app, _, _ = app_and_input(1)
    item = dense_input()
    task = SimpleNamespace(task_id="withdrawn", kind="remember.extract")
    stopped = False

    async def extract(ctx, batch, version, **kwargs):
        nonlocal stopped
        start, end = kwargs["source_ranges"][item.sources[0].source_id]
        entries = ranged_candidates(item, start, end)
        if start > 0 and not stopped:
            stopped = True
            raise RuntimeError("worker stopped")
        return CandidateExtractionResult(
            candidates=entries, model_id="model", policy_version=version
        )

    app.extraction.extract_candidates.side_effect = extract
    with pytest.raises(RuntimeError, match="worker stopped"):
        await consolidation.prepare_candidate_consolidation(app, None, task, (item,))
    assert any(table == "remember_candidate_extractions" for table, _ in app.uow.rows)
    previous_calls = app.extraction.extract_candidates.await_count
    app.final_guard = lambda *args: SimpleNamespace(items=(SimpleNamespace(decision="denied"),))
    with pytest.raises(FoundationError, match="no longer eligible"):
        await consolidation.prepare_candidate_consolidation(app, None, task, (item,))
    assert app.extraction.extract_candidates.await_count == previous_calls
    app.related.assert_not_awaited()
    app.extraction.decide_candidates.assert_not_awaited()
