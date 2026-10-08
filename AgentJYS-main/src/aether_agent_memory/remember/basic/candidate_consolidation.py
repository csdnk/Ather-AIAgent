"""Checkpoint extraction, candidate discovery and bounded LangMem decisions.

Only the final Remember transaction consumes Working inputs. Intermediate
candidates are evidence-bound task data, never persisted long-term memories.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from aether_agent_memory.remember.basic.official_langmem import (
    CandidateExtractionResult,
    CandidateCapacityError,
    ConsolidationResult,
    ExtractedCandidate,
)
from aether_agent_memory.remember.basic.policy import chunks
from aether_agent_memory.remember.contracts.models import MemorySnapshot
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint


def _cost(owner: Any, stage: str, payload: dict[str, Any]) -> int:
    # Measure serialized IDs/evidence as well as text. Leave headroom for the
    # official manager/tool wrappers; this is not a provider context guarantee.
    return (
        owner.tokenizer.count(json.dumps(payload, ensure_ascii=False, default=str))
        + owner.tokenizer.count(owner.extraction.input_instructions(stage))
        + owner.policy.consolidation_context_reserve_tokens
    )


@dataclass(frozen=True)
class _ExtractionPart:
    items: tuple[MemorySnapshot, ...]
    ranges: dict[str, tuple[int, int]]
    depth: int = 0

    def payload(self, owner: Any) -> dict[str, Any]:
        return owner.extraction.extraction_payload(self.items, source_ranges=self.ranges)


def _boundary(text: str, start: int, limit: int) -> int:
    """Prefer a paragraph/sentence end, while making progress for an oversized line."""
    window = text[start:limit]
    floor = max(1, len(window) // 2)
    paragraphs = [m.end() for m in re.finditer(r"(?:\r?\n){2,}", window) if m.end() >= floor]
    if paragraphs:
        return start + paragraphs[-1]
    endings = [
        m.end() for m in re.finditer(r"[。！？!?][”’\"']?\s*|(?<=[.!?])\s+|\r?\n", window)
        if m.end() >= floor
    ]
    return start + endings[-1] if endings else limit


def _ranges(owner: Any, text: str) -> list[tuple[int, int]]:
    result = []
    start = 0
    budget = owner.policy.extraction_chunk_tokens
    while start < len(text):
        # At most budget*8 characters are examined at once; source offsets are
        # never changed by normalization or by an earlier extraction result.
        limit = min(len(text), start + budget * 8)
        piece = chunks(text[start:limit], owner.tokenizer.count, budget)[0]
        end = start + piece[1]
        if end < len(text):
            end = _boundary(text, start, end)
        result.append((start, end))
        start = end
    return result


def _extraction_batches(owner: Any, items: tuple[MemorySnapshot, ...]) -> list[_ExtractionPart]:
    """Group complete short messages, and page a long source without new Working IDs."""
    batches: list[_ExtractionPart] = []
    current: tuple[MemorySnapshot, ...] = ()
    ranges: dict[str, tuple[int, int]] = {}
    tokens = 0
    for item in items:
        for start, end in _ranges(owner, item.content):
            part_ranges = {s.source_id: (start, end) for s in item.sources}
            cost = owner.tokenizer.count(item.content[start:end])
            proposed = _ExtractionPart((*current, item), {**ranges, **part_ranges})
            if current and (
                tokens + cost > owner.policy.extraction_chunk_tokens
                or set(ranges).intersection(part_ranges)
                or _cost(owner, "extraction", proposed.payload(owner)) > owner.policy.comparison_context_tokens
            ):
                batches.append(_ExtractionPart(current, ranges))
                current, ranges, tokens = (), {}, 0
            current = (*current, item)
            ranges = {**ranges, **part_ranges}
            tokens += cost
    if current:
        batches.append(_ExtractionPart(current, ranges))
    return batches


def _split_part(owner: Any, part: _ExtractionPart) -> tuple[_ExtractionPart, _ExtractionPart]:
    if part.depth >= owner.policy.extraction_split_depth:
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "extraction subdivision limit exceeded")
    if len(part.items) > 1:
        middle = len(part.items) // 2
        groups = (part.items[:middle], part.items[middle:])
        return tuple(
            _ExtractionPart(group, {s.source_id: part.ranges[s.source_id] for i in group for s in i.sources}, part.depth + 1)
            for group in groups
        )
    item = part.items[0]
    start, end = part.ranges[item.sources[0].source_id]
    if end - start < 2:
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "minimal source part still exceeds extraction capacity")
    middle = _boundary(item.content, start, start + (end - start) // 2)
    return tuple(
        _ExtractionPart(part.items, {s.source_id: bounds for s in item.sources}, part.depth + 1)
        for bounds in ((start, middle), (middle, end))
    )


def _components(
    candidates: tuple[ExtractedCandidate, ...], related_ids: dict[str, tuple[str, ...]]
) -> list[tuple[ExtractedCandidate, ...]]:
    """Keep all candidates touching one current memory in a single decision."""
    parents = list(range(len(candidates)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    first: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        for old_id in related_ids[candidate.candidate_id]:
            if old_id in first:
                parents[root(index)] = root(first[old_id])
            else:
                first[old_id] = index
    groups: dict[int, list[ExtractedCandidate]] = {}
    for index, candidate in enumerate(candidates):
        groups.setdefault(root(index), []).append(candidate)
    return [tuple(group) for group in groups.values()]


def _decision_inputs(
    candidates: tuple[ExtractedCandidate, ...],
    related_ids: dict[str, tuple[str, ...]],
    existing: dict[str, MemorySnapshot],
) -> tuple[tuple[MemorySnapshot, ...], dict[str, tuple[str, ...]]]:
    mapping = {c.candidate_id: related_ids[c.candidate_id] for c in candidates}
    ids = dict.fromkeys(old_id for values in mapping.values() for old_id in values)
    return tuple(existing[key] for key in ids), mapping


def _decision_batches(
    owner: Any,
    candidates: tuple[ExtractedCandidate, ...],
    related_ids: dict[str, tuple[str, ...]],
    existing: dict[str, MemorySnapshot],
) -> list[tuple[ExtractedCandidate, ...]]:
    def cost(group: tuple[ExtractedCandidate, ...]) -> int:
        old, mapping = _decision_inputs(group, related_ids, existing)
        return _cost(owner, "decision", owner.extraction.decision_payload(group, old, mapping))

    batches: list[tuple[ExtractedCandidate, ...]] = []
    current: tuple[ExtractedCandidate, ...] = ()
    for component in _components(candidates, related_ids):
        if len(component) > 256 or cost(component) > owner.policy.comparison_context_tokens:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION,
                "connected candidate/old-memory group exceeds decision budget; no candidates were consumed",
            )
        if current and (
            len(current) + len(component) > 256
            or cost((*current, *component)) > owner.policy.comparison_context_tokens
        ):
            batches.append(current)
            current = ()
        current = (*current, *component)
    if current:
        batches.append(current)
    return batches


async def prepare_candidate_consolidation(
    owner: Any, ctx: Any, task: Any, items: tuple[MemorySnapshot, ...]
) -> dict[str, Any]:
    if not items:
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "consolidation task has no Working input")
    if any(i.ref.scope != items[0].ref.scope for i in items):
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "mixed consolidation scopes")
    processing_binding = owner.checkpoint_binding()
    with owner.uow.transaction() as tx:
        owner.tasks.guard(tx, task)
        source_binding = owner.comparison_source_binding(tx, items)

    def guard_sources(tx: Any) -> None:
        owner.tasks.guard(tx, task)
        if (
            owner.checkpoint_binding() != processing_binding
            or owner.comparison_source_binding(tx, items) != source_binding
        ):
            raise FoundationError(ErrorCode.RESULT_INVALIDATED, "candidate sources or policy changed")
        if any(
            entry.decision != "allowed"
            for entry in owner.final_guard(tx, ctx, tuple(i.ref for i in items), "recall").items
        ):
            raise FoundationError(ErrorCode.RESULT_INVALIDATED, "Working sources are no longer eligible")

    def consume_extraction_call() -> None:
        with owner.uow.transaction() as tx:
            guard_sources(tx)
        owner.consume_call(task)

    extracted: dict[str, ExtractedCandidate] = {}
    pending_parts = list(reversed(_extraction_batches(owner, items)))
    extraction_parts = 0
    subdivision_count = 0
    extraction_bindings = []
    while pending_parts:
        part = pending_parts.pop()
        batch = part.items
        binding = fingerprint([
            "official_candidate_extraction_v1", task.task_id, task.kind,
            processing_binding, source_binding, part.ranges, part.depth,
            [[i.ref.model_dump(mode="json"), i.content_hash] for i in batch],
        ])
        extraction_bindings.append(binding)
        with owner.uow.transaction() as tx:
            guard_sources(tx)
            saved = tx.read("remember_candidate_extractions", binding)
            split = tx.read("remember_candidate_extraction_splits", binding)
        split_reason = (split or {}).get("reason")
        if split_reason is None and _cost(owner, "extraction", part.payload(owner)) > owner.policy.comparison_context_tokens:
            split_reason = "input_budget"
        result = None
        if split_reason is None:
            try:
                if saved is None:
                    raw_result = await owner.extraction.extract_candidates(
                        ctx, batch, owner.policy.version, source_ranges=part.ranges,
                        on_model_call=consume_extraction_call,
                    )
                    result = CandidateExtractionResult.model_validate(raw_result)
                else:
                    result = CandidateExtractionResult.model_validate(saved)
            except CandidateCapacityError:
                split_reason = "output_capacity"
            if result is not None and len(result.candidates) > owner.policy.max_candidates:
                split_reason = "candidate_capacity"
        if split_reason is not None:
            children = _split_part(owner, part)
            with owner.uow.transaction() as tx:
                guard_sources(tx)
                tx.write("remember_candidate_extraction_splits", binding, {"reason": split_reason})
            # Deterministic subdivision is persisted: retry visits children, not
            # another costly attempt at the known oversized parent.
            pending_parts.extend(reversed(children))
            subdivision_count += 1
            continue
        assert result is not None
        if result.policy_version != owner.policy.version:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "extraction policy mismatch")
        validated = []
        for entry in result.candidates:
            candidate = owner.validate_candidate(entry.candidate, batch)
            if entry.candidate_id != fingerprint([candidate.model_dump(mode="json")]):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unstable candidate identity")
            if any(
                evidence.source.source_id not in part.ranges
                or evidence.start_char < part.ranges[evidence.source.source_id][0]
                or evidence.end_char > part.ranges[evidence.source.source_id][1]
                for evidence in candidate.evidence
            ):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "candidate evidence escaped its extraction part")
            validated.append(entry.model_copy(update={"candidate": candidate}))
        result = result.model_copy(update={"candidates": tuple(validated)})
        with owner.uow.transaction() as tx:
            guard_sources(tx)
            if saved is None:
                tx.write("remember_candidate_extractions", binding, result.model_dump(mode="json"))
        extraction_parts += 1
        for entry in validated:
            if task.kind == "remember.distill" and entry.candidate.kind != "semantic":
                continue
            previous = extracted.get(entry.candidate_id)
            if previous is not None and previous != entry:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "candidate identity collision")
            extracted[entry.candidate_id] = entry
        if len(extracted) > owner.policy.max_task_candidates:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION,
                "task candidate budget exceeded; no candidates were truncated or committed",
            )

    candidates = tuple(extracted.values())
    space_key = owner.space_key(items[0].ref.scope)
    with owner.uow.transaction() as tx:
        guard_sources(tx)
        sequence = tx.read("remember_long_term_seq", space_key) or 0

    def guard_inventory(tx: Any) -> None:
        guard_sources(tx)
        if (tx.read("remember_long_term_seq", space_key) or 0) != sequence:
            raise FoundationError(ErrorCode.RESULT_INVALIDATED, "inventory changed during candidate comparison")

    existing: dict[str, MemorySnapshot] = {}
    related_ids: dict[str, tuple[str, ...]] = {}
    for entry in candidates:
        binding = fingerprint([
            "official_candidate_discovery_v1", task.task_id, processing_binding,
            source_binding, sequence, entry.model_dump(mode="json"),
        ])
        with owner.uow.transaction() as tx:
            guard_inventory(tx)
            saved = tx.read("remember_candidate_discovery", binding)
        if saved is not None:
            with owner.uow.transaction() as tx:
                guard_inventory(tx)
                saved_items = tuple(MemorySnapshot.model_validate(raw) for raw in saved["existing"])
                stale = any(
                    entry.decision != "allowed"
                    for entry in owner.final_guard(tx, ctx, tuple(i.ref for i in saved_items), "recall").items
                ) if saved_items else False
                if not stale:
                    for old in saved_items:
                        try:
                            current = owner.current(tx, old.ref.memory_id)
                        except FoundationError as exc:
                            if exc.code != ErrorCode.NOT_FOUND:
                                raise
                            stale = True
                            break
                        if current.ref != old.ref or current.object_revision != old.object_revision:
                            stale = True
                            break
            if stale:
                saved = None
        if saved is None:
            old_items = await owner.related(ctx, entry.candidate, items[0].ref.scope)
        else:
            old_items = tuple(MemorySnapshot.model_validate(i) for i in saved["existing"])
        ids = []
        for old in old_items:
            if old.ref.scope != items[0].ref.scope:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "candidate retrieval scope mismatch")
            previous = existing.get(old.ref.memory_id)
            if previous is not None and previous != old:
                raise FoundationError(ErrorCode.RESULT_INVALIDATED, "candidate retrieval observed different versions")
            existing[old.ref.memory_id] = old
            ids.append(old.ref.memory_id)
        related_ids[entry.candidate_id] = tuple(dict.fromkeys(ids))
        with owner.uow.transaction() as tx:
            guard_inventory(tx)
            if saved is None:
                tx.write("remember_candidate_discovery", binding, {
                    "candidate_id": entry.candidate_id,
                    "existing": [i.model_dump(mode="json") for i in old_items],
                })

    decision_batches = _decision_batches(owner, candidates, related_ids, existing)
    proposals = []
    decision_bindings = []
    for index, batch in enumerate(decision_batches):
        old, mapping = _decision_inputs(batch, related_ids, existing)

        def guard_decision(tx: Any) -> None:
            guard_inventory(tx)
            if old and any(
                entry.decision != "allowed"
                for entry in owner.final_guard(tx, ctx, tuple(i.ref for i in old), "recall").items
            ):
                raise FoundationError(ErrorCode.RESULT_INVALIDATED, "comparison targets no longer eligible")
            for item in old:
                current = owner.current(tx, item.ref.memory_id)
                if current.ref != item.ref or current.object_revision != item.object_revision:
                    raise FoundationError(ErrorCode.RESULT_INVALIDATED, "comparison target changed")

        def consume_decision_call() -> None:
            with owner.uow.transaction() as tx:
                guard_decision(tx)
            owner.consume_call(task)

        binding = fingerprint([
            "official_candidate_decisions_v1", task.task_id, processing_binding,
            source_binding, sequence, index, mapping,
            [c.model_dump(mode="json") for c in batch],
            [i.model_dump(mode="json") for i in old],
        ])
        decision_bindings.append(binding)
        with owner.uow.transaction() as tx:
            guard_decision(tx)
            saved = tx.read("remember_candidate_decisions", binding)
        if saved is None:
            raw_result = await owner.extraction.decide_candidates(
                ctx, batch, old, owner.policy.version,
                related_ids=mapping, originals=items,
                on_model_call=consume_decision_call,
            )
            result = ConsolidationResult.model_validate(raw_result)
        else:
            result = ConsolidationResult.model_validate(saved)
        if result.policy_version != owner.policy.version:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "decision policy mismatch")
        covered: list[str] = []
        for proposal in result.proposals:
            candidate = owner.validate_candidate(proposal.candidate, items)
            if not proposal.candidate_ids or any(key not in mapping for key in proposal.candidate_ids):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "decision has unknown candidate IDs")
            target_id = proposal.decision.target_id
            if target_id is not None and any(target_id not in mapping[key] for key in proposal.candidate_ids):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "decision target was not related to its candidates")
            covered.extend(proposal.candidate_ids)
            proposals.append(proposal.model_copy(update={"candidate": candidate}))
        if len(covered) != len(batch) or set(covered) != set(mapping):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "decision omitted or repeated candidates")
        with owner.uow.transaction() as tx:
            guard_decision(tx)
            if saved is None:
                tx.write("remember_candidate_decisions", binding, result.model_dump(mode="json"))

    target_ids = [p.decision.target_id for p in proposals if p.decision.target_id is not None]
    if len(set(target_ids)) != len(target_ids):
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "multiple decisions target the same current memory")
    with owner.uow.transaction() as tx:
        guard_inventory(tx)
    return {
        "two_stage": True,
        "proposals": tuple(proposals),
        "candidates": tuple(p.candidate for p in proposals),
        "extracted_candidate_count": len(candidates),
        "validation_items": items,
        "existing": tuple(existing.values()),
        "context_refs": (),
        "expected_space_seq": sequence,
        "source_binding": source_binding,
        "binding": fingerprint([extraction_bindings, decision_bindings, sequence, source_binding]),
        "rejections": [],
        "support_audit": [],
        "discovery": {
            "strategy": "candidate_first",
            "candidate_queries": len(candidates),
            "per_candidate_limit": owner.policy.comparison_candidates,
            "related_ids": related_ids,
            "existing_memory_count": len(existing),
            "extraction_batches": extraction_parts,
            "extraction_subdivisions": subdivision_count,
            "decision_batches": len(decision_batches),
            "complete_candidates": True,
            "input_budget_tokens": owner.policy.comparison_context_tokens,
        },
    }
