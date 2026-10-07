"""Prepare complete-message inputs for official LangMem, without owning commits."""

from __future__ import annotations

from typing import Any

from aether_agent_memory.remember.basic.extraction import EvidenceValidationError
from aether_agent_memory.remember.basic.official_langmem import (
    ConsolidationProposal,
    ConsolidationResult,
)
from aether_agent_memory.remember.basic.policy import chunks
from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    MemoryKind,
    MemoryRef,
    MemorySnapshot,
)
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint


async def prepare_consolidation(
    owner: Any,
    ctx: Any,
    task: Any,
    items: tuple[MemorySnapshot, ...],
    *,
    allow_representations: bool = True,
) -> dict[str, Any]:
    """Read a bounded current set before inference; retain its binding for CAS.

    A whole message is the input unit. Search queries may be chunks, but they never
    replace the complete new messages delivered to the manager. Context refs are
    read-only and remain distinct from the batch's unprocessed refs.
    """
    if not items:
        return {"proposals": (), "candidates": (), "validation_items": (), "existing": ()}

    space_key = owner.space_key(items[0].ref.scope)
    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        sequence = tx.read("remember_space_seq", space_key) or 0
        batch = tx.read("remember_batches", task.task_id) or {}
    context_refs = tuple(MemoryRef.model_validate(r) for r in batch.get("context_refs", ()))
    context: tuple[MemorySnapshot, ...] = ()
    if context_refs:
        loaded = await owner.load_async(ctx, context_refs)
        if {i.ref for i in loaded.items} != set(context_refs):
            raise FoundationError(ErrorCode.RESULT_INVALIDATED, "context version changed")
        context = await owner.source_access.originals(ctx, loaded.items)

    views: dict[str, str] = {}

    async def resolve_views(originals: tuple[MemorySnapshot, ...]) -> None:
        if not allow_representations:
            return
        for item in originals:
            source = item.sources[0]
            if source.source_id in views:
                continue
            with owner.uow.transaction() as tx:
                ref = item.ref
                source_row = tx.read("remember_sources", source.source_id)
                working_id = (source_row or {}).get("working_id")
                if working_id and working_id != ref.memory_id:
                    working = owner.current(tx, working_id)
                    # A corrected Working cannot lend its artifact to an older source.
                    if source not in working.sources:
                        continue
                    ref = working.ref
                artifact = tx.read("remember_artifacts", owner.refkey(ref))
            if (
                artifact
                and artifact.get("memory") == ref.model_dump(mode="json")
                and artifact.get("source_hash") == source.content_hash
                and artifact.get("published") is True
                and artifact.get("declared_use") == "supported_summary"
            ):
                text, _ = await owner.bodies.read(
                    item.ref.scope, ResourceLocation.model_validate(artifact["location"])
                )
                if owner.tokenizer.count(text) <= owner.policy.comparison_context_tokens:
                    views[source.source_id] = text

    def model_view(item: MemorySnapshot) -> str:
        return views.get(item.sources[0].source_id, item.content)

    await resolve_views(items)
    source_tokens = sum(owner.tokenizer.count(model_view(i)) for i in items)
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
            model_view(item),
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

    await resolve_views((*context, *old_evidence))
    # Context and old originals are optional disambiguation. Keep complete units,
    # prioritizing the nearest adjacent context; never truncate a source to fit.
    selected_context = []
    admitted_ids = set(new_ids)
    omitted_sources = []
    for item in reversed(context):
        source_id = item.sources[0].source_id
        cost = 0 if source_id in admitted_ids else owner.tokenizer.count(model_view(item))
        if total_tokens + cost > owner.policy.comparison_context_tokens:
            break
        selected_context.append(item)
        admitted_ids.add(source_id)
        total_tokens += cost
    context = tuple(reversed(selected_context))
    kept_context_ids = {i.ref.memory_id for i in context}
    context_refs = tuple(r for r in context_refs if r.memory_id in kept_context_ids)
    selected_evidence = []
    for item in old_evidence:
        source_id = item.sources[0].source_id
        cost = 0 if source_id in admitted_ids else owner.tokenizer.count(model_view(item))
        if total_tokens + cost > owner.policy.comparison_context_tokens:
            omitted_sources.append(source_id)
            continue
        selected_evidence.append(item)
        admitted_ids.add(source_id)
        total_tokens += cost
    old_evidence = tuple(selected_evidence)
    originals = tuple(
        {i.sources[0].source_id: i for i in (*items, *context, *old_evidence)}.values()
    )
    representations = [
        {"source_id": key, "text": text} for key, text in views.items() if key in admitted_ids
    ]

    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        if (tx.read("remember_space_seq", space_key) or 0) != sequence:
            raise FoundationError(ErrorCode.RESULT_INVALIDATED, "space changed during discovery")
        source_binding = owner.comparison_source_binding(tx, originals)
        binding = fingerprint(
            [
                "official_consolidation_v1",
                task.task_id,
                owner.checkpoint_binding(),
                sequence,
                source_binding,
                [i.model_dump(mode="json") for i in originals],
                [i.model_dump(mode="json") for i in existing],
                representations,
            ]
        )
        saved = tx.read("remember_official_consolidation", binding)
    if saved is None:
        try:
            result = await owner.extraction.consolidate(
                ctx,
                items,
                existing,
                owner.policy.version,
                representations=representations,
                context_items=context,
                existing_evidence=old_evidence,
                on_model_call=lambda: owner.consume_call(task),
            )
        except EvidenceValidationError as exc:
            if (
                not representations
                or exc.feedback["reason"] not in {"quote_not_in_source", "ambiguous_quote"}
                or exc.feedback["source_id"] not in {r["source_id"] for r in representations}
            ):
                raise
            # A faithful paraphrase can still lack a unique original quote.
            # Retry once with whole originals, under the same explicit budget.
            return await prepare_consolidation(owner, ctx, task, items, allow_representations=False)
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
    for proposal in result.proposals:
        candidate = owner.validate_candidate(proposal.candidate, originals)
        if not new_ids.intersection(s.source_id for s in candidate.sources):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "context-only consolidation output")
        if getattr(task, "kind", None) == "remember.distill" and candidate.kind != "semantic":
            continue
        quotes = "\n".join(e.quote for e in candidate.evidence)
        if candidate.text not in quotes:
            if owner.support_verifier is None:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "support verifier required")
            verdict_key = fingerprint([binding, candidate.model_dump(mode="json")])
            with owner.uow.transaction() as tx:
                verdict = tx.read("remember_official_support", verdict_key)
            if verdict is None:
                owner.consume_call(task)
                supported = await owner.support_verifier.verify(ctx, candidate, quotes)
                if type(supported) is not bool:
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid support verdict")
                verdict = {"supported": supported}
                with owner.uow.transaction() as tx:
                    owner.tasks.guard(tx, task)
                    tx.write("remember_official_support", verdict_key, verdict)
            if not verdict["supported"]:
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
        "context_refs": context_refs,
        "expected_space_seq": sequence,
        "source_binding": source_binding,
        "binding": binding,
        "rejections": rejections,
        "discovery": {
            "queries": len(queries),
            "complete_queries": True,
            "per_query_limit": owner.policy.comparison_candidates,
            "omitted_evidence_sources": omitted_sources,
        },
    }
