"""Extraction selects supplied fragments instead of retranscribing evidence."""

import json

import pytest

from aether_agent_memory.remember.basic.llmlingua import labeled_view
from aether_agent_memory.remember.basic.official_langmem import OfficialLangMemConsolidation

from .test_official_langmem import Manager, row, scripted_tool_model, snapshot


def candidate(text, evidence_ids, **extra):
    return {"text": text, "kind": "semantic", "evidence_ids": evidence_ids, **extra}


async def test_original_fragments_bind_messages_crlf_and_shared_evidence():
    one = snapshot("one", "Alice uses Vim.\r\n\r\nBudget is 20, not 30.\r\n")
    two = snapshot("two", "Bob uses Emacs.")
    manager = Manager(
        lambda _: [
            row("one", candidate("Alice uses Vim.", ["e1"])),
            row("two", candidate("Budget is 20.", ["e2"])),
            row("three", candidate("Budget is not 30.", ["e2"])),
            row("four", candidate("Bob uses Emacs.", ["e3"])),
        ]
    )
    adapter = OfficialLangMemConsolidation(manager, "test", extraction_manager=manager)
    result = await adapter.extract_candidates(None, (one, two), "v1")
    supplied = json.loads(manager.calls[0]["messages"][0]["content"])["sources"]
    assert all("text" not in source for source in supplied)
    assert supplied[0]["fragments"] == [
        {"evidence_id": "e1", "text": "Alice uses Vim.\n"},
        {"evidence_id": "e2", "text": "Budget is 20, not 30.\n"},
    ]
    assert supplied[1]["fragments"] == [{"evidence_id": "e3", "text": two.content}]
    assert len(result.candidates) == 4
    for extracted in result.candidates:
        for evidence in extracted.candidate.evidence:
            original = one if evidence.source.source_id == "src_one" else two
            assert original.content[evidence.start_char : evidence.end_char] == evidence.quote
    assert result.candidates[1].candidate.evidence == result.candidates[2].candidate.evidence
    assert result.candidates[1].candidate.evidence[0].quote == "Budget is 20, not 30.\r\n"


async def test_compressed_repeated_lines_use_positions_not_recombined_model_quotes():
    text = (
        "Meeting confirmed: budget must not exceed 20.\r\n"
        "Meeting confirmed: budget must not exceed 20."
    )
    original = snapshot("repeated", "prefix\r\n" + text + "\r\nsuffix")
    words = [
        ("Meeting", 0),
        ("confirmed", 0),
        (":", 1),
        ("budget", 1),
        ("must", 1),
        ("not", 1),
        ("exceed", 1),
        ("20", 1),
        (".", 1),
        ("Meeting", 1),
        ("confirmed", 1),
        (":", 1),
        ("budget", 1),
        ("must", 0),
        ("not", 1),
        ("exceed", 1),
        ("20", 1),
        (".", 1),
    ]
    view = labeled_view(text, words)
    start = len("prefix\r\n")
    manager = Manager(lambda _: [row("one", candidate("Budget must not exceed 20.", ["e2"]))])
    adapter = OfficialLangMemConsolidation(manager, "test", extraction_manager=manager)
    result = await adapter.extract_candidates(
        None,
        (original,),
        "v1",
        source_ranges={"src_repeated": (start, start + len(text))},
        source_views={"src_repeated": view},
    )
    evidence = result.candidates[0].candidate.evidence[0]
    expected_start = start + text.index("\r\n") + 2
    assert evidence.start_char == expected_start
    assert evidence.end_char == start + len(text)
    assert evidence.quote == "Meeting confirmed: budget must not exceed 20."
    assert "must" in evidence.quote and "not" in evidence.quote and "20" in evidence.quote
    payload = json.loads(manager.calls[0]["messages"][0]["content"])
    assert payload["sources"][0]["fragments"][1]["text"] == view.text.splitlines()[1]


@pytest.mark.parametrize(
    "ids,extra,pattern",
    [
        (["e99"], {}, "unknown"),
        (["e1", "e1"], {}, "distinct"),
        ([""], {}, "unknown"),
        (["e1"], {"evidence": [{"source_id": "src_one", "quote": "Alice uses Vim."}]}, "mix"),
        ([], {}, "requires"),
    ],
)
async def test_invalid_fragment_references_fail_closed(ids, extra, pattern):
    item = snapshot("one", "Alice uses Vim.")
    manager = Manager(lambda _: [row("one", candidate(item.content, ids, **extra))])
    adapter = OfficialLangMemConsolidation(manager, "test", extraction_manager=manager)
    with pytest.raises(ValueError, match=pattern):
        await adapter.extract_candidates(None, (item,), "v1")


async def test_fragment_references_are_limited_to_current_supplied_range():
    item = snapshot("one", "Alice uses Vim.\nBob uses Emacs.")
    start = item.content.index("Bob")
    manager = Manager(lambda _: [row("one", candidate("Bob uses Emacs.", ["e1"]))])
    adapter = OfficialLangMemConsolidation(manager, "test", extraction_manager=manager)
    result = await adapter.extract_candidates(
        None, (item,), "v1", source_ranges={"src_one": (start, len(item.content))}
    )
    evidence = result.candidates[0].candidate.evidence[0]
    assert (evidence.start_char, evidence.end_char, evidence.quote) == (
        start,
        len(item.content),
        "Bob uses Emacs.",
    )
    manager.callback = lambda _: [row("one", candidate("Alice uses Vim.", ["e2"]))]
    with pytest.raises(ValueError, match="unknown"):
        await adapter.extract_candidates(
            None, (item,), "v1", source_ranges={"src_one": (start, len(item.content))}
        )


async def test_whitespace_only_source_has_no_selectable_fragment():
    item = snapshot("blank", "\r\n \t\r\n")
    payload = OfficialLangMemConsolidation.extraction_payload((item,))
    assert json.loads(payload["messages"][0]["content"])["sources"][0]["fragments"] == []
    manager = Manager(lambda _: [row("one", candidate("Unsupported fact.", ["e1"]))])
    adapter = OfficialLangMemConsolidation(manager, "test", extraction_manager=manager)
    with pytest.raises(ValueError, match="unknown"):
        await adapter.extract_candidates(None, (item,), "v1")


async def test_real_official_extraction_tool_accepts_fragment_ids():
    model = scripted_tool_model(
        [
            {
                "id": "extract_call",
                "name": "CandidateMemory",
                "args": candidate("Alice uses Vim.", ["e1"]),
            }
        ]
    )
    item = snapshot("one", "Alice uses Vim.")
    result = await OfficialLangMemConsolidation.from_model(model, "test").extract_candidates(
        None, (item,), "v1"
    )
    assert result.candidates[0].candidate.evidence[0].quote == item.content
