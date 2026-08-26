"""B3 application helpers for deriving scheduler candidates from context."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aether_agent_memory.context.models import ContextPack
from aether_agent_memory.core.enums import MemoryType, StorageTier
from aether_agent_memory.core.memory import Memory


def b3_candidates_from_context(context: ContextPack) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    for memory in context.memories:
        recall_score = _bounded_score(float(context.recall_scores.get(memory.id, 0.0)))
        frequency_score = _bounded_score(min(memory.access_count / 10.0, 1.0))
        objects.append(
            {
                "object_id": memory.object_id or memory.id,
                "object_type": f"{memory.type.value}_memory",
                "current_tier": _tier_for_memory(memory).value,
                "size_bytes": len(memory.content.encode("utf-8")),
                "tenant_id": memory.tenant_id,
                "namespace": memory.session_id,
                "access": {
                    "access_frequency": frequency_score,
                    "recency_score": _recency_score(memory),
                    "hit_rate": 1.0 if recall_score > 0 else 0.0,
                    "access_count": memory.access_count,
                    "last_access_time": (
                        memory.last_accessed_at.isoformat()
                        if memory.last_accessed_at is not None
                        else None
                    ),
                },
                "semantic": {
                    "semantic_relevance": recall_score,
                    "importance": _bounded_score(memory.importance),
                    "task_relevance": recall_score,
                },
                "business_priority": _bounded_score(memory.importance),
                "migratable": True,
                "pinned": False,
                "expired": memory.is_expired(),
                "metadata": {
                    "memory_id": memory.id,
                    "memory_type": memory.type.value,
                    "source": memory.source.value,
                    "storage_inference": (
                        "placement" if memory.placement else "memory_type_adapter"
                    ),
                    "execution_type": "control-plane",
                    "physical_migration": "external",
                    "content_preview": memory.content[:160],
                },
            }
        )
    return {
        "request_id": context.request.request_id,
        "trace_id": context.trace_id or context.request.trace_id,
        "source": "context-recall",
        "objects": objects,
        "resource_state": {
            "tiers": {},
            "migration_cost_score": 0.5,
            "network_available": True,
        },
        "context": context.model_dump(mode="json"),
        "notes": [
            "Candidates are derived from the current ContextPack.",
            "Heat score and action are produced only after POST /api/v1/schedules.",
            "Physical Milvus/Redis migration is not asserted by this adapter.",
        ],
    }


def _bounded_score(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 6)


def _recency_score(memory: Memory) -> float:
    timestamp = memory.last_accessed_at or memory.updated_at or memory.created_at
    age_seconds = max((datetime.now(UTC) - timestamp).total_seconds(), 0.0)
    return _bounded_score(1.0 / (1.0 + age_seconds / 3600.0))


def _tier_for_memory(memory: Memory) -> StorageTier:
    if memory.placement is not None:
        return memory.placement.tier
    if memory.type == MemoryType.WORKING:
        return StorageTier.L0_DRAM
    if memory.type == MemoryType.EPISODIC:
        return StorageTier.L2_HDD
    return StorageTier.L3_OBJECT
