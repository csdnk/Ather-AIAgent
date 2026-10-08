"""Prepare the candidate-first LangMem result for Remember's atomic commit.

This module performs local contract/provenance and authority checks only. The
second LangMem phase has already decided the semantic relationship; it must not
fall through the legacy independent equivalence/occurrence model reviewers.
"""

from __future__ import annotations

from typing import Any

from aether_agent_memory.remember.contracts.models import MemoryKind, MemoryRef, MemoryStatus
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash

from .service import memory_ref

TWO_STAGE_COMMIT_SCHEMA = "remember_candidate_decisions_v1"
TWO_STAGE_ACTIONS = frozenset(
    {"create", "conflict", "amend", "correct", "no_change", "equivalent"}
)


def prepare_two_stage_commit(
    owner: Any, ctx: Any, task: Any, items: tuple[Any, ...], formation: dict[str, Any]
) -> dict[str, Any]:
    """Translate only explicitly new two-stage results, never legacy checkpoints."""
    originals = formation["validation_items"]
    if not items or not originals or formation.get("two_stage") is not True:
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid two-stage formation")
    scope = items[0].ref.scope
    for original in originals:
        digest = text_hash(original.content)
        if original.content_hash != digest or any(
            source.content_hash != digest for source in original.sources
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "original source hash mismatch")
        if original.ref.scope != scope:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "cross-scope original source")
    existing = {item.ref.memory_id: item for item in formation["existing"]}
    if len(existing) != len(formation["existing"]):
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "duplicate consolidation target")
    if any(
        old.ref.scope != scope
        or old.status != MemoryStatus.ACTIVE
        or old.kind not in {MemoryKind.SEMANTIC, MemoryKind.EPISODIC}
        for old in existing.values()
    ):
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "ineligible consolidation target")

    proposals: list[list[Any]] = []
    groups: list[list[str]] = []
    covered: set[str] = set()
    targets: set[str] = set()
    for proposal in formation["proposals"]:
        candidate = owner.validate_candidate(proposal.candidate, originals)
        decision = proposal.decision
        candidate_ids = tuple(proposal.candidate_ids)
        if (
            not candidate_ids
            or len(set(candidate_ids)) != len(candidate_ids)
            or covered.intersection(candidate_ids)
        ):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "invalid candidate decision coverage"
            )
        covered.update(candidate_ids)
        if decision.outcome not in TWO_STAGE_ACTIONS:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unsupported two-stage action")
        target = existing.get(decision.target_id)
        if decision.outcome == "create":
            if decision.target_id is not None:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "new memory cannot name a target"
                )
        else:
            if target is None or target.ref.memory_id in targets:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "missing or repeated decision target"
                )
            targets.add(target.ref.memory_id)
            if decision.outcome != "conflict" and target.kind.value != candidate.kind:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "decision target kind mismatch"
                )
        if task.kind == "remember.distill" and candidate.kind != "semantic":
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "distillation must remain semantic")
        groups.append(list(candidate_ids))
        proposals.append(
            [
                candidate.model_dump(mode="json"),
                decision.model_dump(mode="json"),
                target.model_dump(mode="json") if target else None,
            ]
        )
    candidate_count = formation["extracted_candidate_count"]
    if (
        type(candidate_count) is not int
        or not 0 <= candidate_count <= owner.policy.max_task_candidates
        or len(covered) != candidate_count
    ):
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "incomplete candidate decisions")

    space_key = owner.space_key(scope)
    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        batch = tx.read("remember_batches", task.task_id)
        refs = (
            tuple(MemoryRef.model_validate(r) for r in batch["refs"])
            if batch
            else tuple(item.ref for item in items)
        )
        attempt = (tx.read("remember_comparison_retries", task.task_id) or {}).get("attempts", 0)
        if attempt > owner.policy.max_commit_retries:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "comparison retry budget exhausted")
        if (
            (tx.read("remember_long_term_seq", space_key) or 0) != formation["expected_space_seq"]
            or owner.comparison_source_binding(tx, originals) != formation["source_binding"]
            or any(
                owner.current(tx, key).object_revision != old.object_revision
                for key, old in existing.items()
            )
        ):
            raise FoundationError(
                ErrorCode.RESULT_INVALIDATED, "consolidation sources or targets changed"
            )
        for _, decision, raw_target in proposals:
            if decision["outcome"] in {"amend", "correct"}:
                target = existing[raw_target["ref"]["memory_id"]]
                owner.identity.authorize(tx, ctx, Permission.CORRECT, memory_ref(target.ref))
    return {
        "two_stage": True,
        "official_guard": {
            "two_stage": TWO_STAGE_COMMIT_SCHEMA,
            "binding": formation["binding"],
            "refs": [old.ref.model_dump(mode="json") for old in existing.values()],
            "versions": {key: old.object_revision for key, old in existing.items()},
            "sources": [
                source.model_dump(mode="json") for item in originals for source in item.sources
            ],
            "source_binding": formation["source_binding"],
            "discovery": formation["discovery"],
        },
        "attempt": attempt,
        "space_key": space_key,
        "expected_space_seq": formation["expected_space_seq"],
        "refs": [ref.model_dump(mode="json") for ref in refs],
        "virtual": [],
        "candidate_count": candidate_count,
        "candidate_groups": groups,
        "candidate_rejections": [],
        "candidate_support_audit": [],
        "candidate_support_counts": {},
        "proposals": proposals,
    }
