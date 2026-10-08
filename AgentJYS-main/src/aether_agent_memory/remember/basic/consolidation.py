"""Prepare complete-message inputs for official LangMem, without owning commits."""

from __future__ import annotations

from typing import Any

from aether_agent_memory.remember.basic.official_langmem import (
    ConsolidationProposal,
    ConsolidationResult,
)
from aether_agent_memory.remember.basic.policy import chunks
from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    MemoryKind,
    MemorySnapshot,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint


async def candidate_support_audit(
    owner: Any,
    ctx: Any,
    task: Any,
    candidate: CandidateFact,
    *,
    binding: str,
    table: str,
    key: str,
) -> dict[str, Any]:
    """Record disabled extra review after the caller's source/evidence checks.

    CandidateFact.evidence_status='supported' is the historical source-binding
    contract, not an independent model verdict. Keep that verdict separate and
    nullable so disabled review cannot inflate verified accuracy.
    """
    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        saved = tx.read(table, key)
    if saved is not None:
        # Older checkpoints did not record sampling, but did run the verifier.
        status = saved.get("status") or (
            "supported" if saved.get("supported") is True
            else "rejected" if saved.get("supported") is False else "unknown"
        )
        legacy_verdict = True if status == "supported" else False if status == "rejected" else None
        if status == "unknown":
            raise FoundationError(
                ErrorCode.RESULT_INVALIDATED, "historical support verdict is unknown"
            )
        return {
            **saved,
            "status": status,
            "supported": saved.get("supported", legacy_verdict),
            "candidate_hash": saved.get(
                "candidate_hash", fingerprint(candidate.model_dump(mode="json"))
            ),
        }
    audit: dict[str, Any] = {
        "task_id": task.task_id,
        "binding": binding,
        "candidate_hash": fingerprint(candidate.model_dump(mode="json")),
        "sources": [s.model_dump(mode="json") for s in candidate.sources],
        "evidence": [e.model_dump(mode="json") for e in candidate.evidence],
        "supported": None,
        "status": "not_checked",
        "reason": "additional_entailment_review_disabled",
        "source_check": "exact_quote"
        if any(candidate.text in entry.quote for entry in candidate.evidence)
        else "validated_evidence",
    }
    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        tx.write(table, key, audit)
    return audit


async def prepare_consolidation(
    owner: Any,
    ctx: Any,
    task: Any,
    items: tuple[MemorySnapshot, ...],
    *,
    allow_representations: bool = False,
) -> dict[str, Any]:
    """Read a bounded current set before inference; retain its binding for CAS.

    A whole original message is the input unit. The legacy allow_representations
    keyword is ignored: compressed artifacts never replace source content.
    Search queries may be chunks, but they never replace the complete new
    messages delivered to the manager. Previously processed messages are never
    replayed as overlap context; related long-term
    memories and their source evidence remain available for comparison.
    """
    if getattr(owner.extraction, "supports_candidate_pipeline", False):
        from .candidate_consolidation import prepare_candidate_consolidation

        return await prepare_candidate_consolidation(owner, ctx, task, items)
    if not items:
        return {"proposals": (), "candidates": (), "validation_items": (), "existing": ()}

    space_key = owner.space_key(items[0].ref.scope)
    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        sequence = tx.read("remember_space_seq", space_key) or 0

    source_tokens = sum(owner.tokenizer.count(i.content) for i in items)
    if source_tokens > owner.policy.comparison_context_tokens:
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION,
            "complete messages exceed consolidation context budget; no content was truncated",
        )

    discovered: dict[str, MemorySnapshot] = {}
    queries = [
        (item, piece)
        for item in items
        for _, _, piece in chunks(
            item.content,
            owner.tokenizer.count,
            owner.policy.projection_chunk_tokens,
        )
    ]
    for item, query in queries:
        probe = CandidateFact(text=query, sources=item.sources, evidence_status="candidate")
        for old in await owner.related(ctx, probe, item.ref.scope):
            discovered.setdefault(old.ref.memory_id, old)
    existing = tuple(discovered.values())

    old_evidence: tuple[MemorySnapshot, ...] = ()
    if existing:
        # SourceAccess loads authoritative source text only for Working DTOs. The
        # copy is private evidence input, never persisted or returned as Working.
        old_evidence = await owner.source_access.originals(
            ctx, tuple(i.model_copy(update={"kind": MemoryKind.WORKING}) for i in existing)
        )
        old_evidence = tuple({i.sources[0].source_id: i for i in old_evidence}.values())
    new_ids = {s.source_id for i in items for s in i.sources}
    total_tokens = source_tokens + sum(owner.tokenizer.count(i.content) for i in existing)
    if total_tokens > owner.policy.comparison_context_tokens:
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION,
            "complete messages and existing memories exceed consolidation context budget",
        )

    # Existing memories retain source evidence for comparison. Keep complete
    # source units; never truncate an original to fit the remaining budget.
    admitted_ids = set(new_ids)
    omitted_sources = []
    selected_evidence = []
    for item in old_evidence:
        source_id = item.sources[0].source_id
        cost = 0 if source_id in admitted_ids else owner.tokenizer.count(item.content)
        if total_tokens + cost > owner.policy.comparison_context_tokens:
            omitted_sources.append(source_id)
            continue
        selected_evidence.append(item)
        admitted_ids.add(source_id)
        total_tokens += cost
    old_evidence = tuple(selected_evidence)
    originals = tuple(
        {i.sources[0].source_id: i for i in (*items, *old_evidence)}.values()
    )
    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        if (tx.read("remember_space_seq", space_key) or 0) != sequence:
            raise FoundationError(ErrorCode.RESULT_INVALIDATED, "space changed during discovery")
        source_binding = owner.comparison_source_binding(tx, originals)
        binding = fingerprint(
            [
                "official_consolidation_v3_originals_only",
                task.task_id,
                owner.checkpoint_binding(),
                sequence,
                source_binding,
                [i.model_dump(mode="json") for i in originals],
                [i.model_dump(mode="json") for i in existing],
            ]
        )
        saved = tx.read("remember_official_consolidation", binding)
    if saved is None:
        result = await owner.extraction.consolidate(
            ctx,
            items,
            existing,
            owner.policy.version,
            existing_evidence=old_evidence,
            on_model_call=lambda: owner.consume_call(task),
        )
        result = ConsolidationResult.model_validate(result)
        with owner.uow.transaction() as tx:
            owner.tasks.guard(tx, task)
            if (
                tx.read("remember_space_seq", space_key) or 0
            ) == sequence and owner.comparison_source_binding(tx, originals) == source_binding:
                tx.write("remember_official_consolidation", binding, result.model_dump(mode="json"))
    else:
        result = ConsolidationResult.model_validate(saved)

    accepted: list[ConsolidationProposal] = []
    rejections: list[dict[str, str]] = []
    support_audit: list[dict[str, Any]] = []
    for proposal in result.proposals:
        candidate = owner.validate_candidate(proposal.candidate, originals)
        if not new_ids.intersection(s.source_id for s in candidate.sources):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "consolidation output has no new source")
        if getattr(task, "kind", None) == "remember.distill" and candidate.kind != "semantic":
            continue
        verdict_key = fingerprint([binding, candidate.model_dump(mode="json")])
        verdict = await candidate_support_audit(
            owner,
            ctx,
            task,
            candidate,
            binding=binding,
            table="remember_official_support",
            key=verdict_key,
        )
        support_audit.append(verdict)
        if verdict["status"] == "rejected":
            rejections.append(
                {"candidate_hash": verdict_key, "reason": "evidence_does_not_support_claim"}
            )
            continue
        accepted.append(proposal.model_copy(update={"candidate": candidate}))
    if len(accepted) > owner.policy.max_candidates:
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION, "consolidation candidate budget exceeded"
        )
    return {
        "proposals": tuple(accepted),
        "candidates": tuple(p.candidate for p in accepted),
        "validation_items": originals,
        "existing": existing,
        # Retain the result shape for existing commit guards; old batch context
        # refs are intentionally neither read nor supplied to the model.
        "context_refs": (),
        "expected_space_seq": sequence,
        "source_binding": source_binding,
        "binding": binding,
        "rejections": rejections,
        "support_audit": support_audit,
        "discovery": {
            "queries": len(queries),
            "complete_queries": True,
            "per_query_limit": owner.policy.comparison_candidates,
            "omitted_evidence_sources": omitted_sources,
        },
    }
