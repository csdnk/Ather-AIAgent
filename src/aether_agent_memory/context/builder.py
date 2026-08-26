from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING

from aether_agent_memory.context.models import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import RecalledMemory

if TYPE_CHECKING:
    from aether_agent_memory.episodic.manager import MockEpisodicMemoryManager
    from aether_agent_memory.interfaces.managers import MemoryManager
    from aether_agent_memory.semantic.manager import MockSemanticMemoryManager
    from aether_agent_memory.working.manager import MockWorkingMemoryManager

_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(len(text) // _CHARS_PER_TOKEN, 1)


class MockContextPackBuilder:
    def __init__(
        self,
        working: MockWorkingMemoryManager,
        episodic: MockEpisodicMemoryManager,
        semantic: MockSemanticMemoryManager,
    ) -> None:
        self._managers: dict[MemoryType, MemoryManager] = {
            MemoryType.WORKING: working,
            MemoryType.EPISODIC: episodic,
            MemoryType.SEMANTIC: semantic,
        }

    async def build(self, request: ContextRequest) -> ContextPack:
        async def recall_one(
            memory_type: MemoryType,
        ) -> tuple[MemoryType, list[RecalledMemory], float, str | None]:
            manager = self._managers.get(memory_type)
            if manager is None:
                return memory_type, [], 0.0, "memory manager is not configured"
            started = perf_counter()
            try:
                recalled = await asyncio.wait_for(
                    manager.recall(request),
                    timeout=request.deadline_ms / 1000,
                )
                return memory_type, recalled, (perf_counter() - started) * 1000, None
            except TimeoutError:
                return (
                    memory_type,
                    [],
                    (perf_counter() - started) * 1000,
                    "recall deadline exceeded",
                )
            except Exception as exc:
                return (
                    memory_type,
                    [],
                    (perf_counter() - started) * 1000,
                    f"{type(exc).__name__}: {exc}",
                )

        results = await asyncio.gather(
            *(recall_one(memory_type) for memory_type in request.memory_types)
        )
        merged: list[RecalledMemory] = []
        missing_sources: list[str] = []
        degradation_reasons: dict[str, str] = {}
        source_latency_ms: dict[str, float] = {}
        for memory_type, recalled, latency_ms, error in results:
            source_name = memory_type.value
            source_latency_ms[source_name] = round(latency_ms, 3)
            if error is not None:
                missing_sources.append(source_name)
                degradation_reasons[source_name] = error
            merged.extend(recalled)
        merged.sort(key=lambda r: r.score, reverse=True)

        candidates = merged[: request.max_candidates]

        selected: list[RecalledMemory] = []
        seen_content: set[str] = set()
        total_tokens = 0
        for rm in candidates:
            content_key = " ".join(rm.memory.content.split()).casefold()
            if content_key in seen_content:
                continue
            tokens = _estimate_tokens(rm.memory.content)
            if total_tokens + tokens > request.max_tokens:
                continue
            selected.append(rm)
            seen_content.add(content_key)
            total_tokens += tokens

        recall_scores = {rm.memory.id: rm.score for rm in selected}
        lines = [
            f"[{i + 1}] ({rm.memory.type.value}) {rm.memory.content}"
            for i, rm in enumerate(selected)
        ]
        assembled_text = "\n".join(lines)
        memory_refs = [rm.memory.id for rm in selected]
        evidence_refs: list[str] = []
        for rm in selected:
            if rm.memory.placement is not None:
                evidence_refs.append(rm.memory.placement.object_key)
            metadata_refs = rm.memory.metadata.get("evidence_refs", [])
            if isinstance(metadata_refs, list):
                evidence_refs.extend(str(ref) for ref in metadata_refs)
        evidence_refs = list(dict.fromkeys(evidence_refs))
        summary = "\n".join(rm.memory.content for rm in selected[:3])[:1000]

        return ContextPack(
            request=request,
            memories=[rm.memory for rm in selected],
            total_tokens=total_tokens,
            budget_tokens=request.max_tokens,
            recall_scores=recall_scores,
            assembled_text=assembled_text,
            built_at=datetime.now(UTC),
            summary=summary,
            memory_refs=memory_refs,
            evidence_refs=evidence_refs,
            budget_info={
                "used_tokens": total_tokens,
                "budget_tokens": request.max_tokens,
                "remaining_tokens": max(request.max_tokens - total_tokens, 0),
            },
            trace_id=request.trace_id,
            status="degraded" if missing_sources else "ok",
            complete=not missing_sources,
            missing_sources=missing_sources,
            degradation_reasons=degradation_reasons,
            source_latency_ms=source_latency_ms,
        )
