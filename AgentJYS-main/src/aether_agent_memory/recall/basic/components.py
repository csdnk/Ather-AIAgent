"""Pure candidate fusion and context assembly, independent of framework node types."""

from dataclasses import dataclass

from aether_agent_memory.recall.contracts.models import ContextGroup, ContextItem
from aether_agent_memory.remember.contracts.models import MemorySnapshot, SourceRef
from aether_agent_memory.runtime.foundation.common import fingerprint

from .tokenization import TokenCounter


@dataclass(frozen=True)
class RankedMemory:
    memory: MemorySnapshot
    score: float


def render_body(content: str, sources: tuple[SourceRef, ...]) -> str:
    references = ", ".join(f"{source.source_id}@{source.source_version}" for source in sources)
    return content + "\nSources: " + references + "\n"


def fuse(candidates: list[list[MemorySnapshot]]) -> list[RankedMemory]:
    values: dict[str, MemorySnapshot] = {}
    scores: dict[str, float] = {}
    for route in candidates:
        seen: set[str] = set()
        rank = 0
        for item in route:
            key = item.ref.model_dump_json()
            if key in seen:
                continue
            seen.add(key)
            rank += 1
            values[key] = item
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return [
        RankedMemory(values[key], scores[key])
        for key in sorted(scores, key=lambda key: (-scores[key], key))
    ]


def assemble(
    candidates: list[RankedMemory],
    recall_id: str,
    budget: int,
    tokenizer: TokenCounter,
    max_items: int,
) -> tuple[list[ContextGroup], str]:
    groups: list[ContextGroup] = []
    rendered = ""
    for candidate in candidates:
        item = candidate.memory
        fragment = f"[{len(groups) + 1}] " + render_body(item.content, item.sources)
        if tokenizer.count(rendered + fragment) > budget:
            continue
        groups.append(
            ContextGroup(
                group_id=fingerprint([recall_id, item.ref.model_dump_json()]),
                items=(
                    ContextItem(
                        memory=item.ref,
                        content=item.content,
                        sources=item.sources,
                        representation="original",
                    ),
                ),
            )
        )
        rendered += fragment
        if len(groups) == max_items:
            break
    return groups, rendered
