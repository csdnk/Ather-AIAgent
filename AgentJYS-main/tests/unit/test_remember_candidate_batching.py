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
