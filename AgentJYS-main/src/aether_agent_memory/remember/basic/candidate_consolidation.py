"""Checkpoint extraction, candidate discovery and bounded LangMem decisions.

Only the final Remember transaction consumes Working inputs. Intermediate
candidates are evidence-bound task data, never persisted long-term memories.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from langchain_text_splitters import RecursiveCharacterTextSplitter

from aether_agent_memory.remember.basic.extraction import EvidenceValidationError
from aether_agent_memory.remember.basic.llmlingua import (
    CompressionView,
    LLMLinguaPreprocessor,
    should_precompress,
)
from aether_agent_memory.remember.basic.official_langmem import (
    CandidateCapacityError,
    CandidateExtractionResult,
    ConsolidationResult,
    ExtractedCandidate,
    OfficialLangMemConsolidation,
)
from aether_agent_memory.remember.contracts.foundation import DerivedArtifact
from aether_agent_memory.remember.contracts.models import MemoryKind, MemorySnapshot
from aether_agent_memory.remember.langmem_model import LangMemOutputTruncatedError
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint


def _record_stage_failure(
    owner: Any, task: Any, stage: str, binding: str, error: Exception
) -> None:
    """Best-effort bounded technical diagnostics, never exception messages/data.

    This is independent of task retry/commit semantics. A stale task or failed
    diagnostic write must never hide the original provider/validation exception.
    """
    try:
        record = {
            "task_id": task.task_id,
            "stage": stage,
            "binding": binding,
            "exception_type": type(error).__name__,
        }
        if isinstance(error, EvidenceValidationError):
            reason = error.feedback.get("reason")
            # The feedback also contains source IDs, quotes and candidate text.
            # Even reason is allowed only when it is a known application code.
            if reason in {
                "unknown_source_id",
                "quote_not_in_source",
                "quote_not_in_candidate",
                "source_not_in_supplied_ranges",
            }:
                record["reason"] = reason
        elif isinstance(error, FoundationError) and isinstance(error.code, ErrorCode):
            record["code"] = error.code.value
        key = fingerprint([task.task_id, stage, binding])
        with owner.uow.transaction() as tx:
            owner.tasks.guard(tx, task)
            previous = tx.read("remember_stage_failures", key) or {}
            attempts = previous.get("attempts", 0)
            tx.write(
                "remember_stage_failures",
                key,
                {**record, "attempts": (attempts if type(attempts) is int else 0) + 1},
            )
    except Exception:
        # There is deliberately no fallback logger: formatting the original
        # exception can expose provider credentials or model/source contents.
        return


def _cost(owner: Any, stage: str, payload: dict[str, Any]) -> int:
    # Measure serialized IDs/evidence as well as text. Leave headroom for the
    # official manager/tool wrappers; this is not a provider context guarantee.
    return cast(
        int,
        (
            owner.tokenizer.count(json.dumps(payload, ensure_ascii=False, default=str))
            + owner.tokenizer.count(owner.extraction.input_instructions(stage))
            + owner.policy.consolidation_context_reserve_tokens
        ),
    )


@dataclass(frozen=True)
class _ExtractionPart:
    items: tuple[MemorySnapshot, ...]
    ranges: dict[str, tuple[int, int]]
    depth: int = 0

    def payload(
        self, owner: Any, views: dict[str, CompressionView] | None = None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"source_ranges": self.ranges}
        if views:
            kwargs["source_views"] = views
        return cast(
            dict[str, Any],
            owner.extraction.extraction_payload(self.items, **kwargs),
        )


async def _precompressed_views(
    owner: Any, part: _ExtractionPart, guard: Callable[[Any], None], *, ctx: Any, task: Any
) -> dict[str, CompressionView]:
    """Persist CPU inference separately from LangMem calls, per original part."""
    views: dict[str, CompressionView] = {}
    for item in part.items:
        # A batch threshold must never turn many ordinary short Working messages
        # into a single long input eligible for token deletion.
        if item.kind != MemoryKind.WORKING or not should_precompress(owner.policy, item.content):
            continue
        processor = getattr(owner, "llmlingua_preprocessor", None)
        if processor is None:
            processors = getattr(owner, "_llmlingua_processors", {})
            key = fingerprint(
                [
                    owner.policy.llmlingua_model,
                    owner.policy.llmlingua_model_revision,
                    owner.policy.llmlingua_keep_rate,
                ]
            )
            processor = processors.setdefault(key, LLMLinguaPreprocessor(owner.policy))
            processors[key] = processor
            owner._llmlingua_processors = processors
        for source in item.sources:
            start, end = part.ranges[source.source_id]
            binding = fingerprint(
                [
                    "remember_precompression_v1",
                    item.ref.model_dump(mode="json"),
                    source.model_dump(mode="json"),
                    item.content_hash,
                    start,
                    end,
                    owner.policy.model_dump(mode="json"),
                    processor.model_identity,
                ]
            )
            with owner.uow.transaction() as tx:
                guard(tx)
                saved = tx.read("remember_precompression_views", binding)
            if saved is None:
                view = await processor.acompress(item.content[start:end])
                view.validate_source(item.content[start:end])
            else:
                view = CompressionView.model_validate(saved)
                view.validate_source(item.content[start:end])
            # Use the existing durable body store for the real compressed text.
            # Reuse also checks object bytes; a checkpoint cannot mask a missing
            # artifact body. The authority Working body/projection is untouched.
            location = await owner.bodies.persist(ctx, item.ref.scope, view.text)
            artifact = DerivedArtifact(
                artifact_id=binding,
                scope=item.ref.scope,
                source=source,
                kind="compressed",
                task_id=task.task_id,
                strategy_version=processor.model_identity["adapter"],
                location=location.model_copy(update={"kind": "artifact"}),
                quality="not_sampled",
                created_at=owner.identity.clock(),
            )
            record = {
                "artifact": artifact.model_dump(mode="json"),
                "memory": item.ref.model_dump(mode="json"),
                "start_char": start,
                "end_char": end,
                "source_hash": item.content_hash,
                "consumer": "langmem_candidate_extraction",
                "quality_review_enabled": False,
                "original_bytes": view.original_bytes,
                "stored_bytes": view.retained_bytes,
                "intermediate_retention": view.retained_bytes / view.original_bytes,
                "counts_toward_final_compression_factor": False,
                "model_identity": processor.model_identity,
                "mapping_checkpoint": binding,
            }
            with owner.uow.transaction() as tx:
                guard(tx)
                tx.write("remember_precompression_views", binding, view.model_dump(mode="json"))
                prior = tx.read("remember_precompression_artifacts", binding)
                tx.write("remember_precompression_artifacts", binding, prior or record)
                memory_key = owner.refkey(item.ref)
                artifact_ids = tx.read("remember_precompression_manifest", memory_key) or []
                if binding not in artifact_ids:
                    tx.write(
                        "remember_precompression_manifest", memory_key, [*artifact_ids, binding]
                    )
                memory_ids = (
                    tx.read("remember_precompression_memory_manifest", item.ref.memory_id) or []
                )
                if binding not in memory_ids:
                    tx.write(
                        "remember_precompression_memory_manifest",
                        item.ref.memory_id,
                        [*memory_ids, binding],
                    )
            views[source.source_id] = view
    return views


def precompression_artifact_status(
    owner: Any, tx: Any, ctx: Any, item: MemorySnapshot
) -> list[dict[str, Any]]:
    """Derive eligibility from current authority, including historical versions.

    Retained source-derived objects are not erased on source correction/revocation.
    Their usability is computed under the same guard as extraction, so an old
    artifact cannot remain advertised as current after its source is withdrawn.
    """
    ids = tx.read("remember_precompression_memory_manifest", item.ref.memory_id) or []
    decision = owner.final_guard(tx, ctx, (item.ref,), "recall").items[0]
    result = []
    sources = [source.model_dump(mode="json") for source in item.sources]
    for artifact_id in ids:
        row = tx.read("remember_precompression_artifacts", artifact_id)
        if row is None:
            continue
        reason = (
            "memory_version_changed"
            if row["memory"] != item.ref.model_dump(mode="json")
            else "source_changed"
            if row["source_hash"] != item.content_hash or row["artifact"]["source"] not in sources
            else "source_or_memory_ineligible"
            if decision.decision != "allowed"
            else None
        )
        result.append({**row, "eligible": reason is None, "ineligible_reason": reason})
    return result


# Hindsight's paragraph/sentence/word hierarchy, extended with the separators
# recommended by LangChain for text without word boundaries (including Chinese).
# https://github.com/vectorize-io/hindsight/blob/fb11ddfeac4d5fe9e9ffd96ce144a5284ad7f5a5/hindsight-api-slim/hindsight_api/engine/retain/fact_extraction.py
# https://github.com/langchain-ai/langchain/blob/6b5fdfb8049addd7d8eef98e835a3204a26f553c/libs/text-splitters/langchain_text_splitters/character.py
_SOURCE_SEPARATORS = [
    "\r\n\r\n",
    "\n\n",
    "\r\n",
    "\n",
    ". ",
    "! ",
    "? ",
    "。",
    "！",
    "？",
    "．",
    "; ",
    "；",
    ", ",
    "，",
    "、",
    "：",
    " ",
    "\t",
    "\u200b",
    "",
]


def _recursive_pieces(text: str, budget: int, count: Callable[[str], int]) -> list[str]:
    pieces = RecursiveCharacterTextSplitter(
        separators=_SOURCE_SEPARATORS,
        chunk_size=budget,
        chunk_overlap=0,
        length_function=count,
        keep_separator="end",
        strip_whitespace=False,
        is_separator_regex=False,
    ).split_text(text)
    if "".join(pieces) != text or any(not piece for piece in pieces):
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "source splitter changed original text")
    return pieces


def _ranges(owner: Any, text: str, *, char_budget: int | None = None) -> list[tuple[int, int]]:
    """Use official recursive splitting; derive offsets only by exact concatenation."""
    character_limit = char_budget or owner.policy.extraction_chunk_chars
    token_limit = owner.policy.extraction_chunk_tokens
    pending = list(reversed(_recursive_pieces(text, character_limit, len)))
    pieces = []
    while pending:
        piece = pending.pop()
        if owner.tokenizer.count(piece) <= token_limit:
            pieces.append(piece)
            continue
        if len(piece) == 1:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "single character exceeds model token capacity"
            )
        smaller = _recursive_pieces(piece, token_limit, owner.tokenizer.count)
        # Token counts are not necessarily additive across joined substrings.
        # Recount the actual model input, reducing character targets through the
        # same component if its token-based merge cannot make bounded progress.
        if len(smaller) == 1 or any(
            owner.tokenizer.count(value) > token_limit for value in smaller
        ):
            smaller = _recursive_pieces(piece, max(1, len(piece) // 2), len)
        pending.extend(reversed(smaller))
    result = []
    offset = 0
    for piece in pieces:
        result.append((offset, offset + len(piece)))
        offset += len(piece)
    return result


def _extraction_batches(owner: Any, items: tuple[MemorySnapshot, ...]) -> list[_ExtractionPart]:
    """Group complete short messages, and page a long source without new Working IDs."""
    batches: list[_ExtractionPart] = []
    current: tuple[MemorySnapshot, ...] = ()
    ranges: dict[str, tuple[int, int]] = {}
    tokens, characters = 0, 0
    for item in items:
        item_ranges = (
            [(0, len(item.content))]
            if len(item.content.encode("utf-8")) < owner.policy.compression_min_bytes
            else _ranges(owner, item.content)
        )
        for start, end in item_ranges:
            part_ranges = {s.source_id: (start, end) for s in item.sources}
            cost = owner.tokenizer.count(item.content[start:end])
            proposed = _ExtractionPart((*current, item), {**ranges, **part_ranges})
            if current and (
                tokens + cost > owner.policy.extraction_chunk_tokens
                or characters + end - start > owner.policy.extraction_chunk_chars
                or set(ranges).intersection(part_ranges)
                or _cost(owner, "extraction", proposed.payload(owner))
                > owner.policy.comparison_context_tokens
            ):
                batches.append(_ExtractionPart(current, ranges))
                current, ranges, tokens, characters = (), {}, 0, 0
            current = (*current, item)
            ranges = {**ranges, **part_ranges}
            tokens += cost
            characters += end - start
    if current:
        batches.append(_ExtractionPart(current, ranges))
    return batches


def _complete_short_part(owner: Any, part: _ExtractionPart) -> bool:
    short_items = [
        item
        for item in part.items
        if len(item.content.encode("utf-8")) < owner.policy.compression_min_bytes
    ]
    for item in short_items:
        if not item.sources or any(
            part.ranges.get(source.source_id) != (0, len(item.content)) for source in item.sources
        ):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION,
                "incomplete short message source range cannot be extracted or subdivided",
            )
    return len(part.items) == 1 and len(short_items) == 1


def _short_capacity_error() -> FoundationError:
    return FoundationError(
        ErrorCode.CONTRACT_VIOLATION,
        "complete short message exceeds extraction capacity; "
        "source remains unprocessed and was not split within the message",
    )


def _split_part(owner: Any, part: _ExtractionPart) -> tuple[_ExtractionPart, ...]:
    # A short statement's subject and predicate are one semantic unit. An output
    # limit is not evidence that its input can safely be divided by character.
    if _complete_short_part(owner, part):
        raise _short_capacity_error()
    if part.depth >= owner.policy.extraction_split_depth:
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "extraction subdivision limit exceeded")
    if len(part.items) > 1:
        middle = len(part.items) // 2
        groups = (part.items[:middle], part.items[middle:])
        children = tuple(
            _ExtractionPart(
                group,
                {s.source_id: part.ranges[s.source_id] for i in group for s in i.sources},
                part.depth + 1,
            )
            for group in groups
        )
        return children[0], children[1]
    item = part.items[0]
    start, end = part.ranges[item.sources[0].source_id]
    if end - start < 2:
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION, "minimal source part still exceeds extraction capacity"
        )
    ranges = _ranges(
        owner,
        item.content[start:end],
        char_budget=min(owner.policy.extraction_chunk_chars, max(1, (end - start) // 2)),
    )
    return tuple(
        _ExtractionPart(
            part.items,
            {s.source_id: (start + begin, start + finish) for s in item.sources},
            part.depth + 1,
        )
        for begin, finish in ranges
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


def _decision_output_limit(owner: Any) -> int:
    identity = getattr(owner.extraction, "model_identity", {})
    limit = identity.get("max_output_tokens") if isinstance(identity, dict) else None
    # The deployment adapter publishes its actual generation limit. Custom
    # providers without that identity use the same conservative default.
    return limit if type(limit) is int and limit > 0 else 4096


def _decision_output_cost(
    owner: Any,
    candidates: tuple[ExtractedCandidate, ...],
    existing: tuple[MemorySnapshot, ...],
) -> int:
    """Estimate full tool bodies, evidence and IDs, not just input context.

    This is a packing estimate, not a completeness guarantee: the model may
    merge facts or write a longer explanation. Truncation still causes a split.
    The decision model emits batch-local c/e references, not copied source quotes.
    """
    tokens = 256  # Tool-call envelopes and finishing headroom.
    aliases, references, _ = OfficialLangMemConsolidation._decision_references(candidates)
    short_ids = {original: alias for alias, original in aliases.items()}
    for entry in candidates:
        candidate = entry.candidate
        evidence_ids = [
            key for key, (owner_id, _) in references.items() if owner_id == entry.candidate_id
        ]
        body = {
            "text": candidate.text,
            "kind": candidate.kind,
            "candidate_ids": [short_ids[entry.candidate_id]],
            "evidence_ids": evidence_ids,
            "evidence": [],
            "correction_evidence_id": evidence_ids[0] if evidence_ids else None,
            "event_key": candidate.event_key,
            "fact_key": candidate.fact_key,
            "importance_category": candidate.importance_category,
            "relationship": "amend",
        }
        # Reserve explanation/patch overhead per potential decision. Retained
        # old details can appear in amended/no-change bodies as well.
        tokens += owner.tokenizer.count(json.dumps(body, ensure_ascii=False)) + 128
    tokens += sum(
        owner.tokenizer.count(
            json.dumps(
                {"json_doc_id": item.ref.memory_id, "text": item.content}, ensure_ascii=False
            )
        )
        for item in existing
    )
    return int(tokens)


def _split_decision_batch(
    batch: tuple[ExtractedCandidate, ...], related_ids: dict[str, tuple[str, ...]]
) -> tuple[tuple[ExtractedCandidate, ...], tuple[ExtractedCandidate, ...]]:
    components = _components(batch, related_ids)
    if len(components) < 2:
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION,
            "indivisible candidate/current-memory group exceeds decision output capacity; "
            "Working sources remain unprocessed and no decisions were committed",
        )
    # Components, rather than arbitrary candidates, are the indivisible unit.
    # A target must never be compared independently by two result-producing calls.
    middle = min(
        range(1, len(components)),
        key=lambda i: abs(2 * sum(map(len, components[:i])) - len(batch)),
    )
    return (
        tuple(c for group in components[:middle] for c in group),
        tuple(c for group in components[middle:] for c in group),
    )


def _decision_batches(
    owner: Any,
    candidates: tuple[ExtractedCandidate, ...],
    related_ids: dict[str, tuple[str, ...]],
    existing: dict[str, MemorySnapshot],
) -> list[tuple[ExtractedCandidate, ...]]:
    def cost(group: tuple[ExtractedCandidate, ...]) -> int:
        old, mapping = _decision_inputs(group, related_ids, existing)
        return _cost(owner, "decision", owner.extraction.decision_payload(group, old, mapping))

    def output_cost(group: tuple[ExtractedCandidate, ...]) -> int:
        old, _ = _decision_inputs(group, related_ids, existing)
        return _decision_output_cost(owner, group, old)

    batches: list[tuple[ExtractedCandidate, ...]] = []
    current: tuple[ExtractedCandidate, ...] = ()
    for component in _components(candidates, related_ids):
        if len(component) > 256 or cost(component) > owner.policy.comparison_context_tokens:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION,
                "connected candidate/old-memory group exceeds decision budget; "
                "no candidates were consumed",
            )
        if current and (
            len(current) + len(component) > 256
            or len(current) + len(component) > owner.policy.decision_batch_candidates
            or cost((*current, *component)) > owner.policy.comparison_context_tokens
            or output_cost((*current, *component)) > _decision_output_limit(owner)
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
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION, "consolidation task has no Working input"
        )
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
            raise FoundationError(
                ErrorCode.RESULT_INVALIDATED, "candidate sources or policy changed"
            )
        if any(
            entry.decision != "allowed"
            for entry in owner.final_guard(tx, ctx, tuple(i.ref for i in items), "recall").items
        ):
            raise FoundationError(
                ErrorCode.RESULT_INVALIDATED, "Working sources are no longer eligible"
            )

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
        binding = fingerprint(
            [
                "official_candidate_extraction_v1",
                task.task_id,
                task.kind,
                processing_binding,
                source_binding,
                part.ranges,
                part.depth,
                [[i.ref.model_dump(mode="json"), i.content_hash] for i in batch],
            ]
        )
        if binding not in extraction_bindings:
            extraction_bindings.append(binding)
        with owner.uow.transaction() as tx:
            guard_sources(tx)
            saved = tx.read("remember_candidate_extractions", binding)
            split = tx.read("remember_candidate_extraction_splits", binding)
            capacity_failures = tx.read("remember_short_extraction_failures", binding) or 0
        split_reason = (split or {}).get("reason")
        whole_short = _complete_short_part(owner, part)
        if whole_short and saved is not None:
            split_reason = None
        if whole_short and saved is None:
            # Older workers persisted an unsafe split after their first failure.
            # Resume at the complete parent, never replay its partial children.
            if split_reason in {"output_capacity", "candidate_capacity"}:
                capacity_failures = max(1, capacity_failures)
                split_reason = None
            if capacity_failures >= 2:
                raise _short_capacity_error()
        try:
            views = (
                await _precompressed_views(owner, part, guard_sources, ctx=ctx, task=task)
                if split_reason is None and saved is None
                else {}
            )
        except Exception as exc:
            _record_stage_failure(owner, task, "precompression", binding, exc)
            raise
        if (
            split_reason is None
            and saved is None
            and _cost(owner, "extraction", part.payload(owner, views))
            > owner.policy.comparison_context_tokens
        ):
            split_reason = "input_budget"
        result = None
        if split_reason is None:
            try:
                if saved is None:
                    extraction_kwargs: dict[str, Any] = {}
                    if views:
                        extraction_kwargs["source_views"] = views
                    raw_result = await owner.extraction.extract_candidates(
                        ctx,
                        batch,
                        owner.policy.version,
                        source_ranges=part.ranges,
                        on_model_call=consume_extraction_call,
                        **extraction_kwargs,
                    )
                    result = CandidateExtractionResult.model_validate(raw_result)
                else:
                    result = CandidateExtractionResult.model_validate(saved)
            except CandidateCapacityError:
                split_reason = "output_capacity"
            except Exception as exc:
                _record_stage_failure(owner, task, "extraction", binding, exc)
                raise
            if result is not None and len(result.candidates) > owner.policy.max_candidates:
                split_reason = "candidate_capacity"
        if split_reason is not None:
            if whole_short:
                if split_reason in {"output_capacity", "candidate_capacity"}:
                    # One retry of the complete input. Persist exhaustion so a
                    # worker restart cannot repeatedly spend model calls or
                    # turn failure into a partial successful memory.
                    capacity_failures += 1
                    with owner.uow.transaction() as tx:
                        guard_sources(tx)
                        tx.write("remember_short_extraction_failures", binding, capacity_failures)
                    if capacity_failures < 2:
                        pending_parts.append(part)
                        continue
                raise _short_capacity_error()
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
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "candidate evidence escaped its extraction part"
                )
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
            raise FoundationError(
                ErrorCode.RESULT_INVALIDATED, "inventory changed during candidate comparison"
            )

    existing: dict[str, MemorySnapshot] = {}
    related_ids: dict[str, tuple[str, ...]] = {}
    for entry in candidates:
        binding = fingerprint(
            [
                "official_candidate_discovery_v1",
                task.task_id,
                processing_binding,
                source_binding,
                sequence,
                entry.model_dump(mode="json"),
            ]
        )
        with owner.uow.transaction() as tx:
            guard_inventory(tx)
            saved = tx.read("remember_candidate_discovery", binding)
        if saved is not None:
            with owner.uow.transaction() as tx:
                guard_inventory(tx)
                saved_items = tuple(MemorySnapshot.model_validate(raw) for raw in saved["existing"])
                stale = (
                    any(
                        entry.decision != "allowed"
                        for entry in owner.final_guard(
                            tx, ctx, tuple(i.ref for i in saved_items), "recall"
                        ).items
                    )
                    if saved_items
                    else False
                )
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
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "candidate retrieval scope mismatch"
                )
            prior_memory = existing.get(old.ref.memory_id)
            if prior_memory is not None and prior_memory != old:
                raise FoundationError(
                    ErrorCode.RESULT_INVALIDATED, "candidate retrieval observed different versions"
                )
            existing[old.ref.memory_id] = old
            ids.append(old.ref.memory_id)
        related_ids[entry.candidate_id] = tuple(dict.fromkeys(ids))
        with owner.uow.transaction() as tx:
            guard_inventory(tx)
            if saved is None:
                tx.write(
                    "remember_candidate_discovery",
                    binding,
                    {
                        "candidate_id": entry.candidate_id,
                        "existing": [i.model_dump(mode="json") for i in old_items],
                    },
                )

    decision_batches = _decision_batches(owner, candidates, related_ids, existing)
    pending_decisions = list(reversed(decision_batches))
    completed_decisions = 0
    decision_subdivisions = 0
    proposals = []
    decision_bindings = []
    while pending_decisions:
        decision_batch = pending_decisions.pop()
        comparison_items, mapping = _decision_inputs(decision_batch, related_ids, existing)

        def guard_decision(tx: Any, targets: tuple[MemorySnapshot, ...] = comparison_items) -> None:
            guard_inventory(tx)
            if targets and any(
                entry.decision != "allowed"
                for entry in owner.final_guard(
                    tx, ctx, tuple(i.ref for i in targets), "recall"
                ).items
            ):
                raise FoundationError(
                    ErrorCode.RESULT_INVALIDATED, "comparison targets no longer eligible"
                )
            for item in targets:
                current = owner.current(tx, item.ref.memory_id)
                if current.ref != item.ref or current.object_revision != item.object_revision:
                    raise FoundationError(ErrorCode.RESULT_INVALIDATED, "comparison target changed")

        def consume_decision_call(guard: Callable[[Any], None] = guard_decision) -> None:
            with owner.uow.transaction() as tx:
                guard(tx)
            owner.consume_call(task)

        binding = fingerprint(
            [
                "official_candidate_decisions_v2_output_batches",
                task.task_id,
                processing_binding,
                source_binding,
                sequence,
                _decision_output_limit(owner),
                mapping,
                [c.model_dump(mode="json") for c in decision_batch],
                [i.model_dump(mode="json") for i in comparison_items],
            ]
        )
        decision_bindings.append(binding)
        with owner.uow.transaction() as tx:
            guard_decision(tx)
            saved = tx.read("remember_candidate_decisions", binding)
            split = tx.read("remember_candidate_decision_splits", binding)
        split_reason = (split or {}).get("reason")
        decision_result = None
        if split_reason is None:
            try:
                if saved is None:
                    raw_result = await owner.extraction.decide_candidates(
                        ctx,
                        decision_batch,
                        comparison_items,
                        owner.policy.version,
                        related_ids=mapping,
                        originals=items,
                        on_model_call=consume_decision_call,
                    )
                    decision_result = ConsolidationResult.model_validate(raw_result)
                else:
                    decision_result = ConsolidationResult.model_validate(saved)
            except LangMemOutputTruncatedError:
                split_reason = "output_capacity"
            except Exception as exc:
                _record_stage_failure(owner, task, "decision", binding, exc)
                raise
        if split_reason is not None:
            with owner.uow.transaction() as tx:
                guard_decision(tx)
                tx.write("remember_candidate_decision_splits", binding, {"reason": split_reason})
            # Persist before subdivision, including an indivisible terminal
            # failure. A retry never invokes the known oversized parent again;
            # successful children retain their own independent checkpoints.
            decision_children = _split_decision_batch(decision_batch, related_ids)
            pending_decisions.extend(reversed(decision_children))
            decision_subdivisions += 1
            continue
        assert decision_result is not None
        if decision_result.policy_version != owner.policy.version:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "decision policy mismatch")
        covered: list[str] = []
        for proposal in decision_result.proposals:
            candidate = owner.validate_candidate(proposal.candidate, items)
            if not proposal.candidate_ids or any(
                key not in mapping for key in proposal.candidate_ids
            ):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "decision has unknown candidate IDs"
                )
            target_id = proposal.decision.target_id
            if target_id is not None and any(
                target_id not in mapping[key] for key in proposal.candidate_ids
            ):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "decision target was not related to its candidates",
                )
            covered.extend(proposal.candidate_ids)
            proposals.append(proposal.model_copy(update={"candidate": candidate}))
        if len(covered) != len(decision_batch) or set(covered) != set(mapping):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "decision omitted or repeated candidates"
            )
        with owner.uow.transaction() as tx:
            guard_decision(tx)
            if saved is None:
                tx.write(
                    "remember_candidate_decisions",
                    binding,
                    decision_result.model_dump(mode="json"),
                )
        completed_decisions += 1

    target_ids = [p.decision.target_id for p in proposals if p.decision.target_id is not None]
    if len(set(target_ids)) != len(target_ids):
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION, "multiple decisions target the same current memory"
        )
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
            "decision_batches": completed_decisions,
            "decision_subdivisions": decision_subdivisions,
            "output_budget_tokens": _decision_output_limit(owner),
            "complete_candidates": True,
            "input_budget_tokens": owner.policy.comparison_context_tokens,
        },
    }
