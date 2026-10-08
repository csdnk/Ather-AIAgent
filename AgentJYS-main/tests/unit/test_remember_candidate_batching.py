"""Bound decision output without dropping candidates or splitting shared targets."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aether_agent_memory.remember.basic.candidate_consolidation import (
    _decision_batches,
    prepare_candidate_consolidation,
)
from aether_agent_memory.remember.basic.comparison import ComparisonDecision
from aether_agent_memory.remember.basic.official_langmem import (
    CandidateCapacityError,
    CandidateExtractionResult,
    ConsolidationProposal,
    ConsolidationResult,
    ExtractedCandidate,
    OfficialLangMemConsolidation,
    _ToolProposalGuard,
)
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    FactEvidence,
    MemoryKind,
)
from aether_agent_memory.remember.langmem_model import LangMemOutputTruncatedError
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint

from .test_official_langmem import snapshot


def test_official_callback_marks_truncated_decision_for_subdivision():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    guard = _ToolProposalGuard({}, None)
    guard.on_llm_end(
        LLMResult(
            generations=[
                [
                    ChatGeneration(
                        message=AIMessage(content="", response_metadata={"finish_reason": "length"})
                    )
                ]
            ]
        )
    )
    assert isinstance(guard.error, LangMemOutputTruncatedError)


class Store:
    def __init__(self):
        self.rows = {}

    @contextmanager
    def transaction(self):
        yield self

    def read(self, table, key):
        return self.rows.get((table, key))

    def write(self, table, key, value):
        self.rows[(table, key)] = value


def candidate(item):
    fact = CandidateFact(
        text=item.content,
        kind="semantic",
        sources=item.sources,
        evidence_status="supported",
        evidence=(
            FactEvidence(
                source=item.sources[0],
                start_char=0,
                end_char=len(item.content),
                quote=item.content,
            ),
        ),
    )
    return ExtractedCandidate(
        candidate_id=fingerprint([fact.model_dump(mode="json")]),
        candidate=fact,
    )


def app_and_input(count=4, *, output_tokens=100_000, old=None):
    items = tuple(snapshot(f"new{i}", f"Person {i} uses editor {i}.") for i in range(count))
    facts = tuple(candidate(item) for item in items)
    policy = RememberPolicy()

    async def extract(ctx, batch, version, **kwargs):
        return CandidateExtractionResult(
            candidates=tuple(candidate(item) for item in batch),
            model_id="model",
            policy_version=version,
        )

    async def decide(ctx, batch, existing, version, **kwargs):
        return ConsolidationResult(
            proposals=tuple(
                ConsolidationProposal(
                    candidate=entry.candidate,
                    decision=ComparisonDecision(outcome="create", reason="new fact"),
                    candidate_ids=(entry.candidate_id,),
                    reason="new fact",
                )
                for entry in batch
            ),
            model_id="model",
            policy_version=version,
        )

    app = SimpleNamespace(
        uow=Store(),
        policy=policy,
        tokenizer=SimpleNamespace(count=lambda text: (len(text) + 3) // 4),
        extraction=SimpleNamespace(
            model_identity={"max_output_tokens": output_tokens},
            input_instructions=OfficialLangMemConsolidation.input_instructions,
            extraction_payload=OfficialLangMemConsolidation.extraction_payload,
            decision_payload=OfficialLangMemConsolidation.decision_payload,
            extract_candidates=AsyncMock(side_effect=extract),
            decide_candidates=AsyncMock(side_effect=decide),
        ),
        related=AsyncMock(return_value=(old,) if old else ()),
        current=lambda tx, key: old,
        tasks=SimpleNamespace(guard=lambda *args: None),
        checkpoint_binding=lambda: "processing-v1",
        comparison_source_binding=lambda tx, values: "sources-v1",
        consume_call=lambda task: None,
        space_key=lambda scope: "space",
        final_guard=lambda tx, ctx, refs, action: SimpleNamespace(
            items=tuple(SimpleNamespace(decision="allowed") for ref in refs)
        ),
        validate_candidate=lambda value, originals: value,
    )
    return app, items, facts


def test_31_independent_candidates_are_prebatched_for_output_limit():
    app, _, facts = app_and_input(31, output_tokens=4096)
    related = {entry.candidate_id: () for entry in facts}
    batches = _decision_batches(app, facts, related, {})
    assert len(batches) > 1
    assert tuple(entry for batch in batches for entry in batch) == facts


def test_prebatching_never_splits_candidates_sharing_a_current_memory():
    app, _, facts = app_and_input(8, output_tokens=1000)
    old = snapshot("old", "Earlier preferences.", MemoryKind.SEMANTIC)
    related = {entry.candidate_id: ("old",) if i < 2 else () for i, entry in enumerate(facts)}
    batches = _decision_batches(app, facts, related, {"old": old})
    assert any(all(entry in batch for entry in facts[:2]) for batch in batches)
    assert len(batches) > 1


async def test_truncation_splits_and_resume_reuses_saved_child_decisions():
    app, items, facts = app_and_input()
    task = SimpleNamespace(task_id="task", kind="remember.extract")
    delegate = app.extraction.decide_candidates.side_effect
    calls = []
    interrupted = False

    async def decide(ctx, batch, existing, version, **kwargs):
        nonlocal interrupted
        calls.append(tuple(entry.candidate_id for entry in batch))
        if len(batch) > 2:
            raise LangMemOutputTruncatedError("output truncated")
        if batch[0] == facts[2] and not interrupted:
            interrupted = True
            raise RuntimeError("process stopped after first child checkpoint")
        return await delegate(ctx, batch, existing, version, **kwargs)

    app.extraction.decide_candidates.side_effect = decide
    with pytest.raises(RuntimeError, match="process stopped"):
        await prepare_candidate_consolidation(app, None, task, items)
    result = await prepare_candidate_consolidation(app, None, task, items)
    assert [len(batch) for batch in calls] == [4, 2, 2, 2]
    assert {cid for p in result["proposals"] for cid in p.candidate_ids} == {
        entry.candidate_id for entry in facts
    }
    assert result["discovery"]["decision_batches"] == 2
    assert result["discovery"]["decision_subdivisions"] == 1
    assert app.extraction.extract_candidates.await_count == 1
    assert app.related.await_count == 4


async def test_truncated_indivisible_group_is_persisted_without_retrying_model():
    old = snapshot("old", "Earlier preferences.", MemoryKind.SEMANTIC)
    app, items, _ = app_and_input(old=old)
    app.extraction.decide_candidates.side_effect = LangMemOutputTruncatedError("output truncated")
    task = SimpleNamespace(task_id="task", kind="remember.extract")
    for _ in range(2):
        with pytest.raises(FoundationError, match="indivisible.*decision.*output"):
            await prepare_candidate_consolidation(app, None, task, items)
    assert app.extraction.decide_candidates.await_count == 1
    assert not any(table == "remember_candidate_decisions" for table, _ in app.uow.rows)


async def test_recursive_splits_cover_every_candidate_and_replay_without_calls():
    app, items, facts = app_and_input(8)
    delegate = app.extraction.decide_candidates.side_effect

    async def decide(ctx, batch, existing, version, **kwargs):
        if len(batch) > 1:
            raise LangMemOutputTruncatedError("output truncated")
        return await delegate(ctx, batch, existing, version, **kwargs)

    app.extraction.decide_candidates.side_effect = decide
    task = SimpleNamespace(task_id="task", kind="remember.extract")
    first = await prepare_candidate_consolidation(app, None, task, items)
    resumed = await prepare_candidate_consolidation(app, None, task, items)
    assert resumed == first
    assert first["discovery"]["decision_batches"] == 8
    assert first["discovery"]["decision_subdivisions"] == 7
    assert app.extraction.decide_candidates.await_count == 15
    assert [cid for proposal in first["proposals"] for cid in proposal.candidate_ids] == [
        entry.candidate_id for entry in facts
    ]


async def test_changed_inventory_cannot_reuse_decisions_from_previous_snapshot():
    app, items, _ = app_and_input(2)
    task = SimpleNamespace(task_id="task", kind="remember.extract")
    await prepare_candidate_consolidation(app, None, task, items)
    app.uow.write("remember_long_term_seq", "space", 1)
    await prepare_candidate_consolidation(app, None, task, items)
    assert app.extraction.extract_candidates.await_count == 1
    assert app.extraction.decide_candidates.await_count == 2
    assert app.related.await_count == 4


async def test_truncated_short_preference_retries_whole_message_without_losing_subject():
    app, _, _ = app_and_input(1)
    item = snapshot("short", "沈舟的首选编辑器是VS Code。")
    calls = []

    async def extract(ctx, batch, version, **kwargs):
        bounds = kwargs["source_ranges"][item.sources[0].source_id]
        calls.append(bounds)
        if len(calls) == 1:
            raise CandidateCapacityError("output truncated")
        # The real Azure failure returned only [8,17): it could no longer know
        # whose preference this was. Every retry must retain the whole statement.
        assert bounds == (0, len(item.content))
        return CandidateExtractionResult(
            candidates=(candidate(item),), model_id="model", policy_version=version
        )

    app.extraction.extract_candidates.side_effect = extract
    task = SimpleNamespace(task_id="short-task", kind="remember.extract")
    result = await prepare_candidate_consolidation(app, None, task, (item,))
    assert calls == [(0, len(item.content))] * 2
    assert result["proposals"][0].candidate.text == item.content
    assert result["discovery"]["extraction_subdivisions"] == 0
    assert await prepare_candidate_consolidation(app, None, task, (item,)) == result
    assert len(calls) == 2


async def test_short_output_retry_limit_survives_resume_without_committing_partial_memory():
    app, _, _ = app_and_input(1)
    item = snapshot("short", "沈舟的首选编辑器是VS Code。")
    app.extraction.extract_candidates.side_effect = CandidateCapacityError("output truncated")
    task = SimpleNamespace(task_id="short-task", kind="remember.extract")
    for _ in range(2):
        with pytest.raises(FoundationError, match="complete short.*capacity"):
            await prepare_candidate_consolidation(app, None, task, (item,))
    assert app.extraction.extract_candidates.await_count == 2
    app.related.assert_not_awaited()
    app.extraction.decide_candidates.assert_not_awaited()
    assert not any(table == "remember_candidate_extractions" for table, _ in app.uow.rows)


async def test_resume_legacy_short_split_reprocesses_parent_not_partial_children():
    app, _, _ = app_and_input(1)
    item = snapshot("short", "沈舟的首选编辑器是VS Code。")
    task = SimpleNamespace(task_id="short-task", kind="remember.extract")
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
    app.uow.write("remember_candidate_extraction_splits", binding, {"reason": "output_capacity"})
    result = await prepare_candidate_consolidation(app, None, task, (item,))
    app.extraction.extract_candidates.assert_awaited_once()
    assert app.extraction.extract_candidates.call_args.kwargs["source_ranges"] == {
        item.sources[0].source_id: (0, len(item.content))
    }
    assert result["proposals"][0].candidate.text == item.content
    assert await prepare_candidate_consolidation(app, None, task, (item,)) == result


def test_large_source_subdivision_still_covers_the_complete_original():
    from aether_agent_memory.remember.basic.candidate_consolidation import (
        _ExtractionPart,
        _split_part,
    )

    app, _, _ = app_and_input(1)
    item = snapshot("long", "完整长记忆。" * 600)
    source_id = item.sources[0].source_id
    parent = _ExtractionPart((item,), {source_id: (0, len(item.content))})
    left, right = _split_part(app, parent)
    assert left.ranges[source_id][0] == 0
    assert left.ranges[source_id][1] == right.ranges[source_id][0]
    assert right.ranges[source_id][1] == len(item.content)


async def test_short_input_budget_failure_does_not_split_or_call_model():
    app, items, _ = app_and_input(1)
    app.policy = app.policy.model_copy(update={"comparison_context_tokens": 1})
    task = SimpleNamespace(task_id="short-task", kind="remember.extract")
    with pytest.raises(FoundationError, match="complete short.*capacity"):
        await prepare_candidate_consolidation(app, None, task, items)
    app.extraction.extract_candidates.assert_not_awaited()


async def test_output_truncation_splits_short_batch_only_between_whole_messages():
    app, items, _ = app_and_input(2)
    delegate = app.extraction.extract_candidates.side_effect
    calls = []

    async def extract(ctx, batch, version, **kwargs):
        calls.append(tuple(item.ref.memory_id for item in batch))
        if len(batch) > 1:
            raise CandidateCapacityError("output truncated")
        assert kwargs["source_ranges"] == {
            item.sources[0].source_id: (0, len(item.content)) for item in batch
        }
        return await delegate(ctx, batch, version, **kwargs)

    app.extraction.extract_candidates.side_effect = extract
    task = SimpleNamespace(task_id="short-batch-task", kind="remember.extract")
    result = await prepare_candidate_consolidation(app, None, task, items)
    assert [len(batch) for batch in calls] == [2, 1, 1]
    assert len(result["proposals"]) == 2


async def test_short_message_over_chunk_token_limit_is_never_initially_fragmented():
    from aether_agent_memory.remember.basic.candidate_consolidation import _extraction_batches

    app, _, _ = app_and_input(1)
    app.policy = app.policy.model_copy(update={"extraction_chunk_tokens": 16})
    app.tokenizer = SimpleNamespace(count=len)
    item = snapshot(
        "short-token-heavy", "沈舟明确表示他的首选编辑器仍然是VS Code，并且目前不打算更换。"
    )
    assert len(item.content) > app.policy.extraction_chunk_tokens
    assert len(item.content.encode("utf-8")) < app.policy.compression_min_bytes
    parts = _extraction_batches(app, (item,))
    assert len(parts) == 1
    assert parts[0].ranges == {item.sources[0].source_id: (0, len(item.content))}
    task = SimpleNamespace(task_id="token-heavy-short-task", kind="remember.extract")
    result = await prepare_candidate_consolidation(app, None, task, (item,))
    assert app.extraction.extract_candidates.await_count == 1
    assert result["proposals"][0].candidate.text == item.content


def test_short_fragment_range_is_rejected_instead_of_classified_as_complete():
    from aether_agent_memory.remember.basic.candidate_consolidation import (
        _complete_short_part,
        _ExtractionPart,
        _split_part,
    )

    app, items, _ = app_and_input(1)
    part = _ExtractionPart(items, {items[0].sources[0].source_id: (2, len(items[0].content))})
    with pytest.raises(FoundationError, match="incomplete short"):
        _complete_short_part(app, part)
    with pytest.raises(FoundationError, match="incomplete short"):
        _split_part(app, part)


def test_hindsight_sized_decision_batches_keep_shared_target_groups_whole():
    app, _, facts = app_and_input(20)
    assert app.policy.decision_batch_candidates == 8
    related = {entry.candidate_id: () for entry in facts}
    assert [len(group) for group in _decision_batches(app, facts, related, {})] == [8, 8, 4]
    old = snapshot("old", "Shared event.", MemoryKind.SEMANTIC)
    related = {entry.candidate_id: ("old",) if i < 9 else () for i, entry in enumerate(facts)}
    grouped = _decision_batches(app, facts, related, {"old": old})
    assert [len(group) for group in grouped] == [9, 8, 3]
    assert grouped[0] == facts[:9]


def test_recursive_character_ranges_are_bounded_exact_and_preserve_source_whitespace():
    from aether_agent_memory.remember.basic.candidate_consolidation import _ranges

    app, _, _ = app_and_input(1)
    assert app.policy.extraction_chunk_chars == 3000
    text = ("  首句保留条件，不得公开。\r\n\r\n第二句有数字18；负责人是林澈！\n" * 200) + "\t尾部  "
    ranges = _ranges(app, text)
    assert len(ranges) > 1
    assert "".join(text[start:end] for start, end in ranges) == text
    assert all(0 < end - start <= 3000 for start, end in ranges)
    assert all(left[1] == right[0] for left, right in zip(ranges[:-1], ranges[1:], strict=True))
    assert all(text[:end].endswith(("\r\n\r\n", "\n", "  ")) for _, end in ranges)


def test_recursive_ranges_enforce_actual_model_tokens_and_reject_oversized_character():
    from aether_agent_memory.remember.basic.candidate_consolidation import _ranges

    app, _, _ = app_and_input(1)
    app.policy = app.policy.model_copy(update={"extraction_chunk_tokens": 16})
    app.tokenizer = SimpleNamespace(count=lambda text: len(text) * 4)
    text = "密集🙂内容" * 20
    ranges = _ranges(app, text)
    assert "".join(text[start:end] for start, end in ranges) == text
    assert all(app.tokenizer.count(text[start:end]) <= 16 for start, end in ranges)
    app.tokenizer = SimpleNamespace(count=lambda text: len(text) * 17)
    with pytest.raises(FoundationError, match="single character.*token"):
        _ranges(app, "🙂")


def test_short_batch_character_target_does_not_split_one_complete_message():
    from aether_agent_memory.remember.basic.candidate_consolidation import _extraction_batches

    app, _, _ = app_and_input(1)
    first, second = snapshot("a", "a" * 1800), snapshot("b", "b" * 1800)
    batches = _extraction_batches(app, (first, second))
    assert [len(batch.items) for batch in batches] == [1, 1]
    oversized_target = snapshot("c", "x" * 4000)
    batches = _extraction_batches(app, (oversized_target,))
    assert len(batches) == 1
    assert batches[0].ranges == {"src_c": (0, 4000)}


def test_decision_output_estimate_counts_evidence_references_not_copied_source_quotes():
    from aether_agent_memory.remember.basic.candidate_consolidation import _decision_output_cost

    app, _, _ = app_and_input(1)
    estimates = []
    for original in ("Alice uses Linux.", "A long source quote. " * 400):
        item = candidate(snapshot("source", original))
        fact = item.candidate.model_copy(update={"text": "Alice uses Linux."})
        item = ExtractedCandidate(
            candidate_id=fingerprint([fact.model_dump(mode="json")]), candidate=fact
        )
        estimates.append(_decision_output_cost(app, (item,), ()))
    assert estimates[0] == estimates[1]
