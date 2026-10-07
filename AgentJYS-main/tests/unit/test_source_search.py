"""Source search uses actual, authorized originals before any compression work."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aether_agent_memory.remember.basic.source_search import SourceSearch
from aether_agent_memory.remember.contracts.models import SourceRef, SourceSearchRequest
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash


def fixture(text):
    source = SourceRef(
        source_id="source", source_version=1, content_hash=text_hash(text), locator="original"
    )
    state = {"valid": True, "revision": 1, "reads": 0}

    @contextmanager
    def transaction():
        yield None

    def checked(tx, ctx, ref):
        if ctx != "alice":
            raise FoundationError(ErrorCode.FORBIDDEN, "unauthorized")
        if not state["valid"] or ref != source:
            raise FoundationError(ErrorCode.MEMORY_GONE, "revoked")
        return dict(state)

    async def read(ctx, ref, start=0, end=None):
        checked(None, ctx, ref)
        state["reads"] += 1
        stop = min(len(text), start + 256)
        return {"content": text[start:stop], "next_start": stop if stop < len(text) else None}

    owner = SimpleNamespace(
        uow=SimpleNamespace(transaction=transaction),
        source_access=SimpleNamespace(checked=checked, read=read),
    )
    return SourceSearch(owner), source, state


def test_search_chinese_name_beyond_intro_and_exact_provenance():
    text = (
        "普通项目背景，不包含业务负责人。\n\n" * 200
    ) + "霁川计划负责人是顾思远，预算为七百万元。\n\n后续安排待定。"
    search, source, _ = fixture(text)
    result = asyncio.run(
        search.search("alice", SourceSearchRequest(sources=(source,), query="霁川 顾思远"))
    )
    assert result.passages
    hit = result.passages[0]
    assert "顾思远" in hit.content and hit.start_char > 1000
    assert text[hit.start_char : hit.end_char] == hit.content
    assert hit.range_hash == text_hash(hit.content) and hit.source == source


def test_no_match_returns_no_descriptor_or_invented_answer():
    search, source, _ = fixture("背景介绍。\n\n负责人顾思远。")
    assert not asyncio.run(
        search.search("alice", SourceSearchRequest(sources=(source,), query="不存在的火星术语"))
    ).passages


def test_whole_chunk_budget_and_neighbor_dedup():
    text = "无关引言。\n\n顾思远负责霁川。\n\n顾思远管理采购。\n\n最后说明。"
    search, source, _ = fixture(text)
    result = asyncio.run(
        search.search(
            "alice",
            SourceSearchRequest(
                sources=(source,), query="顾思远", neighbor_chunks=1, max_chars=30, max_passages=3
            ),
        )
    )
    spans = [(x.start_char, x.end_char) for x in result.passages]
    assert len(spans) == len(set(spans)) <= 3
    assert sum(len(x.content) for x in result.passages) <= 30
    assert all(text[x.start_char : x.end_char] == x.content for x in result.passages)
    assert not asyncio.run(
        search.search("alice", SourceSearchRequest(sources=(source,), query="顾思远", max_chars=1))
    ).passages


@pytest.mark.parametrize("user,revoked", [("bob", False), ("alice", True)])
def test_authorize_before_original_io(user, revoked):
    search, source, state = fixture("顾思远。")
    state["valid"] = not revoked
    with pytest.raises(FoundationError):
        asyncio.run(search.search(user, SourceSearchRequest(sources=(source,), query="顾思远")))
    assert state["reads"] == 0


def test_revoke_during_io_invalidates_entire_result():
    search, source, state = fixture("顾思远。")
    read = search.owner.source_access.read

    async def raced(*args):
        result = await read(*args)
        state["valid"] = False
        return result

    search.owner.source_access.read = raced
    with pytest.raises(FoundationError):
        asyncio.run(search.search("alice", SourceSearchRequest(sources=(source,), query="顾思远")))


@pytest.mark.parametrize(
    "update",
    [
        {"query": "x" * 513},
        {"max_passages": 13},
        {"max_chars": 20001},
        {"neighbor_chunks": 2},
        {"query": "  "},
    ],
)
def test_search_contract_limits(update):
    _, source, _ = fixture("test")
    with pytest.raises(ValidationError):
        SourceSearchRequest.model_validate({"sources": (source,), "query": "test", **update})


def test_source_count_and_duplicate_contract():
    _, source, _ = fixture("test")
    for sources in (
        (),
        (source, source),
        tuple(source.model_copy(update={"source_id": f"s{i}"}) for i in range(9)),
    ):
        with pytest.raises(ValidationError):
            SourceSearchRequest(sources=sources, query="test")


def test_revision_change_after_ranking_invalidates_results(monkeypatch):
    from aether_agent_memory.remember.basic import source_search

    search, source, state = fixture("顾思远负责人。")
    rank = source_search.rank_sources

    def changed(*args):
        result = rank(*args)
        state["revision"] += 1
        return result

    monkeypatch.setattr(source_search, "rank_sources", changed)
    with pytest.raises(FoundationError) as error:
        asyncio.run(search.search("alice", SourceSearchRequest(sources=(source,), query="顾思远")))
    assert error.value.code == ErrorCode.RESULT_INVALIDATED


def test_corrupted_original_and_stale_ref_fail_closed():
    search, source, state = fixture("顾思远负责人。")
    with pytest.raises(FoundationError):
        asyncio.run(
            search.search(
                "alice",
                SourceSearchRequest(
                    sources=(source.model_copy(update={"source_version": 2}),), query="顾思远"
                ),
            )
        )
    assert state["reads"] == 0

    async def corrupted(*args):
        return {"content": "篡改原文", "next_start": None}

    search.owner.source_access.read = corrupted
    with pytest.raises(FoundationError) as error:
        asyncio.run(search.search("alice", SourceSearchRequest(sources=(source,), query="顾思远")))
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


def test_retrieval_unit_boundaries_preserve_text_and_neighbors_cannot_cross_source():
    from aether_agent_memory.remember.basic.source_search import rank_sources, source_units

    text = "short paragraph\n\n" + "long paragraph " * 200
    units = source_units(text)
    assert units[0][2] == "short paragraph\n\n"
    assert "".join(unit[2] for unit in units) == text
    assert all(text[start:end] == part and len(part) <= 1024 for start, end, part in units)
    _, first, _ = fixture("needle")
    second = first.model_copy(
        update={"source_id": "second", "content_hash": text_hash("unrelated")}
    )
    result = rank_sources(
        [(first, "needle"), (second, "unrelated")],
        SourceSearchRequest(sources=(first, second), query="needle", neighbor_chunks=1),
    )
    assert [p.source for p in result.passages] == [first]
