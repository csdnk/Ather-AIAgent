"""Repeated literal evidence is valid and remains bound to authorized source ranges."""

import json

import pytest

from aether_agent_memory.remember.basic.extraction import EvidenceValidationError
from aether_agent_memory.remember.basic.official_langmem import (
    CandidateMemory,
    ConsolidatedMemory,
    OfficialLangMemConsolidation,
)
from aether_agent_memory.remember.contracts.models import MemoryKind
from unit.test_official_langmem import Manager, row, snapshot


def extractor(fact):
    manager = Manager(lambda _: [row("generated", fact)])
    return OfficialLangMemConsolidation(
        manager, "test", extraction_manager=manager, decision_manager=manager
    )


async def test_repeated_quote_binds_every_exact_occurrence_without_duplicate_candidates():
    quote = "林澈负责发布。"
    original = snapshot("repeat", (quote + "\r\n") * 18)
    value = CandidateMemory(
        text=quote,
        kind="semantic",
        evidence=[{"source_id": "src_repeat", "quote": quote}],
    )
    result = await extractor(value).extract_candidates(None, (original,), "p1")
    assert len(result.candidates) == 1
    evidence = result.candidates[0].candidate.evidence
    assert len(evidence) == 18
    assert [entry.start_char for entry in evidence] == [i * (len(quote) + 2) for i in range(18)]
    assert all(original.content[e.start_char : e.end_char] == e.quote == quote for e in evidence)
    assert all(e.source == original.sources[0] for e in evidence)


async def test_repeated_quote_is_bound_only_inside_the_submitted_processing_range():
    quote = "Alice uses Linux."
    original = snapshot("repeat", (quote + "\n") * 4)
    value = CandidateMemory(
        text=quote,
        kind="semantic",
        evidence=[{"source_id": "src_repeat", "quote": quote}],
    )
    start = len(quote) + 1
    end = 3 * start
    result = await extractor(value).extract_candidates(
        None, (original,), "p1", source_ranges={"src_repeat": (start, end)}
    )
    assert [(e.start_char, e.end_char) for e in result.candidates[0].candidate.evidence] == [
        (start, start + len(quote)),
        (2 * start, 2 * start + len(quote)),
    ]


async def test_optional_offsets_can_select_a_real_later_occurrence():
    quote = "Alice uses Linux."
    original = snapshot("repeat", quote + "\n" + quote)
    start = len(quote) + 1
    value = CandidateMemory(
        text=quote,
        kind="semantic",
        evidence=[
            {
                "source_id": "src_repeat",
                "quote": quote,
                "start_char": start,
                "end_char": len(original.content),
            }
        ],
    )
    result = await extractor(value).extract_candidates(None, (original,), "p1")
    assert [(e.start_char, e.end_char) for e in result.candidates[0].candidate.evidence] == [
        (start, len(original.content))
    ]


async def test_repeated_quotes_do_not_authorize_text_absent_from_the_source():
    original = snapshot("repeat", "Alice uses Linux.\n" * 4)
    value = CandidateMemory(
        text="Alice uses Windows.",
        kind="semantic",
        evidence=[{"source_id": "src_repeat", "quote": "Alice uses Windows."}],
    )
    with pytest.raises(EvidenceValidationError, match="quote_not_in_source"):
        await extractor(value).extract_candidates(None, (original,), "p1")


@pytest.mark.parametrize("correct", [False, True])
async def test_decision_accepts_repeated_bound_evidence_without_requiring_numeric_offsets(correct):
    quote = "Correction: Alice now uses Linux."
    original = snapshot("repeat", (quote + "\n") * 3)
    value = CandidateMemory(
        text="Alice uses Linux.",
        kind="semantic",
        evidence=[{"source_id": "src_repeat", "quote": quote}],
    )
    adapter = extractor(value)
    candidates = (await adapter.extract_candidates(None, (original,), "p1")).candidates
    old = snapshot("old", "Alice uses Windows.", MemoryKind.SEMANTIC)
    decision = ConsolidatedMemory(
        **value.model_dump(),
        relationship="correct" if correct else "create",
        candidate_ids=(candidates[0].candidate_id,),
        reason="The source explicitly corrects the earlier preference.",
        correction_evidence={"source_id": "src_repeat", "quote": quote} if correct else None,
    )
    adapter.decision_manager = Manager(lambda _: [row("old" if correct else "created", decision)])
    result = await adapter.decide_candidates(
        None,
        candidates,
        (old,) if correct else (),
        "p1",
        related_ids={candidates[0].candidate_id: ("old",) if correct else ()},
        originals=(original,),
    )
    assert result.proposals[0].decision.outcome == ("correct" if correct else "create")
    assert result.proposals[0].candidate.evidence == candidates[0].candidate.evidence


async def test_decision_payload_shows_a_repeated_quote_once_and_keeps_all_positions():
    quote = "Alice uses Linux.\r\nOnly for work."
    original = snapshot("repeat", (quote + "\r\n") * 150)
    value = CandidateMemory(
        text="Alice uses Linux only for work.",
        kind="semantic",
        evidence=[{"source_id": "src_repeat", "quote": quote.replace("\r\n", "\n")}],
    )
    adapter = extractor(value)
    candidates = (await adapter.extract_candidates(None, (original,), "p1")).candidates
    payload = adapter.decision_payload(candidates, (), {candidates[0].candidate_id: ()})
    evidence = json.loads(payload["messages"][0]["content"])["candidates"][0]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["quote"] == quote.replace("\r\n", "\n")
    assert len(evidence[0]["spans"]) == 150
    for span in evidence[0]["spans"]:
        assert original.content[span["start_char"] : span["end_char"]] == quote


async def test_explicit_position_outside_processing_range_is_not_authorized_by_same_quote():
    quote = "Alice uses Linux."
    original = snapshot("repeat", (quote + "\n") * 2)
    value = CandidateMemory(
        text=quote,
        kind="semantic",
        evidence=[
            {"source_id": "src_repeat", "quote": quote, "start_char": 0, "end_char": len(quote)}
        ],
    )
    with pytest.raises(ValueError, match="offsets do not match"):
        await extractor(value).extract_candidates(
            None,
            (original,),
            "p1",
            source_ranges={"src_repeat": (len(quote) + 1, len(original.content))},
        )


async def test_repeated_evidence_does_not_bypass_source_hash_verification():
    original = snapshot("repeat", "Alice uses Linux.\n" * 3)
    tampered = original.model_copy(update={"content": "Alice uses Windows.\n" * 3})
    value = CandidateMemory(
        text="Alice uses Windows.",
        kind="semantic",
        evidence=[{"source_id": "src_repeat", "quote": "Alice uses Windows."}],
    )
    with pytest.raises(ValueError, match="source original bytes"):
        await extractor(value).extract_candidates(None, (tampered,), "p1")


async def short_reference_inputs():
    originals = (
        snapshot("one", "Correction: Alice now uses Linux.\n" * 3),
        snapshot("two", "Alice uses Linux only for work."),
    )
    adapter = extractor(None)
    candidates = []
    for original in originals:
        adapter.extraction_manager = Manager(
            lambda _, original=original: [
                row(
                    "generated",
                    CandidateMemory(
                        text=original.content.splitlines()[0],
                        kind="semantic",
                        evidence=[
                            {
                                "source_id": original.sources[0].source_id,
                                "quote": original.content.splitlines()[0],
                            }
                        ],
                    ),
                )
            ]
        )
        candidates.extend((await adapter.extract_candidates(None, (original,), "p1")).candidates)
    return adapter, originals, tuple(candidates)


async def test_short_decision_ids_restore_all_grouped_evidence_and_duplicate_positions():
    adapter, originals, candidates = await short_reference_inputs()
    payload = adapter.decision_payload(candidates, (), {c.candidate_id: () for c in candidates})
    sent = json.loads(payload["messages"][0]["content"])
    assert [c["candidate_id"] for c in sent["candidates"]] == ["c1", "c2"]
    assert set(sent["related_ids"]) == {"c1", "c2"}
    assert [c["evidence"][0]["evidence_id"] for c in sent["candidates"]] == ["e1", "e2"]
    assert "source_id" not in sent["candidates"][0]["evidence"][0]
    decision = ConsolidatedMemory(
        text="Alice now uses Linux only for work.",
        kind="semantic",
        candidate_ids=("c1", "c2"),
        evidence_ids=("e1", "e2"),
        reason="Compatible details.",
    )
    adapter.decision_manager = Manager(lambda _: [row("new", decision)])
    result = await adapter.decide_candidates(
        None,
        candidates,
        (),
        "p1",
        originals=originals,
        related_ids={c.candidate_id: () for c in candidates},
    )
    proposal = result.proposals[0]
    assert proposal.candidate_ids == tuple(c.candidate_id for c in candidates)
    assert proposal.candidate.evidence == tuple(e for c in candidates for e in c.candidate.evidence)


@pytest.mark.parametrize("references", [("e999",), ("e2",)])
async def test_short_evidence_reference_rejects_unknown_or_uncovered_candidate(references):
    adapter, originals, candidates = await short_reference_inputs()
    decision = ConsolidatedMemory(
        text=candidates[0].candidate.text,
        kind="semantic",
        candidate_ids=("c1",),
        evidence_ids=references,
        reason="New fact.",
    )
    adapter.decision_manager = Manager(lambda _: [row("new", decision)])
    with pytest.raises(ValueError, match="evidence reference"):
        await adapter.decide_candidates(
            None,
            candidates,
            (),
            "p1",
            originals=originals,
            related_ids={c.candidate_id: () for c in candidates},
        )


async def test_correction_short_evidence_id_resolves_to_original_quote():
    adapter, originals, candidates = await short_reference_inputs()
    candidates = candidates[:1]
    old = snapshot("old", "Alice uses Windows.", MemoryKind.SEMANTIC)
    decision = ConsolidatedMemory(
        text="Alice now uses Linux.",
        kind="semantic",
        relationship="correct",
        candidate_ids=("c1",),
        evidence_ids=("e1",),
        correction_evidence_id="e1",
        reason="An explicit correction of the earlier preference.",
    )
    adapter.decision_manager = Manager(lambda _: [row("old", decision)])
    result = await adapter.decide_candidates(
        None,
        candidates,
        (old,),
        "p1",
        originals=originals,
        related_ids={candidates[0].candidate_id: ("old",)},
    )
    assert result.proposals[0].decision.evidence_quote == "Correction: Alice now uses Linux."
    assert result.proposals[0].candidate.evidence == candidates[0].candidate.evidence


@pytest.mark.parametrize("invalid", ["missing_group_evidence", "mixed_legacy", "bad_correction"])
async def test_short_references_do_not_weaken_coverage_or_correction_checks(invalid):
    adapter, originals, candidates = await short_reference_inputs()
    decision = ConsolidatedMemory(
        text="Alice now uses Linux only for work.",
        kind="semantic",
        candidate_ids=("c1", "c2") if invalid == "missing_group_evidence" else ("c1",),
        evidence_ids=("e1",),
        reason="Candidate-supported fact.",
        evidence=[{"source_id": "src_one", "quote": originals[0].content.splitlines()[0]}]
        if invalid == "mixed_legacy"
        else [],
        correction_evidence_id="e2" if invalid == "bad_correction" else None,
    )
    adapter.decision_manager = Manager(lambda _: [row("new", decision)])
    with pytest.raises(ValueError, match="every covered|cannot mix|correction evidence reference"):
        await adapter.decide_candidates(
            None,
            candidates,
            (),
            "p1",
            originals=originals,
            related_ids={c.candidate_id: () for c in candidates},
        )


@pytest.mark.parametrize("correct", [False, True])
async def test_official_manager_supports_short_evidence_tools_and_patches(correct):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    _, originals, candidates = await short_reference_inputs()
    candidates = candidates[:1]
    old = snapshot("old", "Alice uses Windows.", MemoryKind.SEMANTIC)
    decision = ConsolidatedMemory(
        text="Alice now uses Linux.",
        kind="semantic",
        candidate_ids=("c1",),
        evidence_ids=("e1",),
        relationship="correct" if correct else "create",
        correction_evidence_id="e1" if correct else None,
        reason="Explicit new preference.",
    )

    class ScriptedModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "evidence-reference-tool-test"

        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=tools, **kwargs)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            args = decision.model_dump(mode="json")
            if correct:
                args = {
                    "json_doc_id": "old",
                    "planned_edits": "Apply explicit correction.",
                    "patches": [
                        {"op": "replace", "path": "/" + key, "value": value}
                        for key, value in args.items()
                    ],
                }
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "call-reference",
                                    "name": "PatchDoc" if correct else "ConsolidatedMemory",
                                    "args": args,
                                }
                            ],
                        )
                    )
                ]
            )

    adapter = OfficialLangMemConsolidation.from_model(ScriptedModel(), "reference-test")
    result = await adapter.decide_candidates(
        None,
        candidates,
        (old,) if correct else (),
        "p1",
        originals=originals,
        related_ids={candidates[0].candidate_id: ("old",) if correct else ()},
    )
    assert result.proposals[0].candidate_ids == (candidates[0].candidate_id,)
    assert result.proposals[0].candidate.evidence == candidates[0].candidate.evidence
    assert result.proposals[0].decision.outcome == ("correct" if correct else "create")
