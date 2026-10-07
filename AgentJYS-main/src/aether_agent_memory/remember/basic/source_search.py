"""Cold source-scoped BM25 retrieval over verified original text, outside DB locks.

The initial implementation builds an ephemeral index on demand. No raw source is
duplicated in metadata and no descriptor, compressed artifact or LLM is searched.
Retrieval units are slices of originals, never consolidation message boundaries.
"""

import asyncio
import re
from typing import Any

import jieba
from rank_bm25 import BM25Plus

from aether_agent_memory.remember.contracts.models import (
    SourcePassage,
    SourceRef,
    SourceSearchRequest,
    SourceSearchResult,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash

_WORDS = re.compile(r"[\w]+", re.UNICODE)
_CHINESE = re.compile(r"[\u3400-\u9fff]+")
_STOP = frozenset(("的", "是", "了", "和", "与", "在", "the", "a", "an", "of", "is"))


def tokens(text: str) -> list[str]:
    # No probabilistic HMM segmentation. Bigrams retain unseen Chinese names
    # that a fixed dictionary might otherwise split into single characters.
    value = text.casefold()
    parts = [
        p for p in jieba.cut_for_search(value, HMM=False) if _WORDS.fullmatch(p) and p not in _STOP
    ]
    parts.extend(run[i : i + 2] for run in _CHINESE.findall(value) for i in range(len(run) - 1))
    return parts


def source_units(text: str) -> list[tuple[int, int, str]]:
    result = []
    start = 0
    for boundary in [m.end() for m in re.finditer(r"\n\s*\n", text)] + [len(text)]:
        for offset in range(start, boundary, 1024):
            end = min(boundary, offset + 1024)
            if text[offset:end].strip():
                result.append((offset, end, text[offset:end]))
        start = boundary
    return result


def rank_sources(
    originals: list[tuple[SourceRef, str]], request: SourceSearchRequest
) -> SourceSearchResult:
    units = [
        (source, start, end, part, index)
        for source, text in originals
        for index, (start, end, part) in enumerate(source_units(text))
    ]
    query = tokens(request.query)
    if not units or not query:
        return SourceSearchResult(passages=())
    corpus = [tokens(unit[3]) for unit in units]
    if not any(corpus):
        return SourceSearchResult(passages=())
    scores = BM25Plus(corpus).get_scores(query)
    query_set = set(query)
    ranked = sorted(
        (i for i, words in enumerate(corpus) if query_set.intersection(words)),
        key=lambda i: (-float(scores[i]), i),
    )
    passages = []
    admitted = set()
    chars = 0

    def admit(i: int, seed: bool) -> bool:
        nonlocal chars
        source, start, end, part, _ = units[i]
        if (
            i in admitted
            or len(passages) >= request.max_passages
            or chars + len(part) > request.max_chars
        ):
            return False
        passages.append(
            SourcePassage(
                source=source,
                content=part,
                start_char=start,
                end_char=end,
                range_hash=text_hash(part),
                score=float(scores[i]) if seed else 0,
                seed=seed,
            )
        )
        admitted.add(i)
        chars += len(part)
        return True

    # Keep capacity for actual hits first; then at most one adjacent unit per seed.
    seeds = []
    for i in ranked:
        if admit(i, True):
            seeds.append(i)
    if request.neighbor_chunks:
        for i in seeds:
            for neighbor in (i + 1, i - 1):
                if (
                    0 <= neighbor < len(units)
                    and units[neighbor][0] == units[i][0]
                    and admit(neighbor, False)
                ):
                    break
    return SourceSearchResult(passages=tuple(passages))


class SourceSearch:
    def __init__(self, owner: Any) -> None:
        self.owner = owner

    async def search(self, ctx: TrustedContext, request: SourceSearchRequest) -> SourceSearchResult:
        def bindings() -> list[int]:
            with self.owner.uow.transaction() as tx:
                return [
                    self.owner.source_access.checked(tx, ctx, source)["revision"]
                    for source in request.sources
                ]

        revisions = await asyncio.to_thread(bindings)
        originals = []
        for source in request.sources:
            parts = []
            start = 0
            while True:
                page = await self.owner.source_access.read(ctx, source, start)
                parts.append(page["content"])
                if page["next_start"] is None:
                    break
                start = page["next_start"]
            text = "".join(parts)
            if text_hash(text) != source.content_hash:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "source search hash mismatch")
            originals.append((source, text))
        result = await asyncio.to_thread(rank_sources, originals, request)
        # This final guard covers every source, including earlier sources revoked
        # while a later source was read or while BM25 computed its result.
        if await asyncio.to_thread(bindings) != revisions:
            raise FoundationError(ErrorCode.RESULT_INVALIDATED, "source changed during search")
        return result
