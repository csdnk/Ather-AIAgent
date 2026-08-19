"""Enterprise-style structured memory extraction and consolidation.

The long-term-memory path keeps the original conversation as evidence, while
also materialising compact facts that can be deduplicated, revised and
retrieved independently.  The extractor is deterministic by default so the
feature works without a second LLM call; an application can replace it with a
model-backed extractor through the small ``FactExtractor`` protocol.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from aether_agent_memory.b2.text_classifier import TextClassification, classify_text
from aether_agent_memory.core.enums import MemoryState, MemoryType
from aether_agent_memory.core.memory import Memory, MemoryFact

if TYPE_CHECKING:
    from aether_agent_memory.interfaces.managers import MemoryManager


_SENTENCE_PATTERN = re.compile(r".+?(?:[。！？!?；;\n]+|$)", re.S)
_NUMBER_PATTERN = re.compile(r"\d")
_DATE_PATTERN = re.compile(r"(20\d{2})[-年](\d{1,2})[-月](\d{1,2})")
_SPACE_PATTERN = re.compile(r"\s+")
_PUNCTUATION_PATTERN = re.compile(r"[\s，。！？；：,.!?;:、()\[\]{}<>《》「」『』\"'“”‘’]+")

_KIND_BY_CATEGORY = {
    "user_preference": "preference",
    "tool_result": "tool_fact",
    "rag_evidence": "evidence",
    "task": "task",
    "document_knowledge": "knowledge",
    "conversation": "conversation",
    "unknown": "conversation",
}


@dataclass(frozen=True)
class FactExtractionPolicy:
    """Controls the deterministic fallback extractor."""

    algorithm: str = "b2-structured-memory-consolidation"
    version: str = "1.0"
    max_facts: int = 8
    min_fact_confidence: float = 0.35

    def __post_init__(self) -> None:
        if self.max_facts <= 0:
            raise ValueError("max_facts must be positive")
        if not 0.0 <= self.min_fact_confidence <= 1.0:
            raise ValueError("min_fact_confidence must be between 0 and 1")


class FactExtractor(Protocol):
    def extract(self, memory: Memory) -> list[MemoryFact]: ...


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_PATTERN.findall(text) if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _normalize(value: str) -> str:
    value = _SPACE_PATTERN.sub("", value).casefold()
    return _PUNCTUATION_PATTERN.sub("", value)


def _date_from_text(value: str) -> datetime | None:
    match = _DATE_PATTERN.search(value)
    if match is None:
        return None
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=UTC,
        )
    except ValueError:
        return None


def _evidence_refs(memory: Memory) -> list[str]:
    refs: list[str] = []
    if memory.p2_ref is not None:
        refs.append(memory.p2_ref.object_key)
    metadata_refs = memory.metadata.get("evidence_refs", [])
    if isinstance(metadata_refs, Sequence) and not isinstance(metadata_refs, (str, bytes)):
        refs.extend(str(ref) for ref in metadata_refs)
    return list(dict.fromkeys(refs))


class StructuredFactExtractor:
    """Extract typed, provenance-carrying facts without an extra model call."""

    def __init__(self, policy: FactExtractionPolicy | None = None) -> None:
        self.policy = policy or FactExtractionPolicy()

    def extract(self, memory: Memory) -> list[MemoryFact]:
        text = memory.content.strip()
        if not text:
            return []
        classification = classify_text(text)
        kind = _KIND_BY_CATEGORY.get(classification.category, "conversation")
        keywords = [str(item) for item in memory.metadata.get("keywords", [])]
        evidence_refs = _evidence_refs(memory)
        subject = memory.user_id or f"session:{memory.session_id}"
        sentence_candidates = self._rank_sentences(
            _sentences(text), classification=classification, keywords=keywords
        )
        facts: list[MemoryFact] = []
        for sentence, score in sentence_candidates[: self.policy.max_facts]:
            confidence = min(0.99, max(self.policy.min_fact_confidence, score))
            marker = next(
                (
                    term
                    for term in (*classification.keywords, *keywords)
                    if term and term.casefold() in sentence.casefold()
                ),
                None,
            )
            predicate = f"{kind}:{marker or 'statement'}"
            normalized_sentence = _normalize(sentence)
            group_key = _stable_key(kind, subject, _normalize(marker or kind))
            normalized_key = _stable_key(group_key, normalized_sentence)
            facts.append(
                MemoryFact(
                    kind=kind,
                    subject=subject,
                    predicate=predicate,
                    value=sentence,
                    normalized_key=normalized_key,
                    group_key=group_key,
                    confidence=round(confidence, 6),
                    source_memory_id=memory.id,
                    source_id=memory.source_id,
                    evidence_refs=evidence_refs,
                    valid_from=_date_from_text(sentence),
                )
            )
        return facts

    @staticmethod
    def _rank_sentences(
        sentences: list[str],
        *,
        classification: TextClassification,
        keywords: list[str],
    ) -> list[tuple[str, float]]:
        ranked: list[tuple[str, float, int]] = []
        terms = (*classification.keywords, *keywords)
        for index, sentence in enumerate(sentences):
            normalized = sentence.casefold()
            score = classification.confidence
            score += 0.18 * sum(term.casefold() in normalized for term in terms if term)
            score += 0.12 * len(_NUMBER_PATTERN.findall(sentence))
            score += 0.08 if index in {0, len(sentences) - 1} else 0.0
            score += min(len(sentence) / 2000.0, 0.08)
            ranked.append((sentence, min(score, 0.99), index))
        ranked.sort(key=lambda item: (-item[1], item[2]))
        return [(sentence, score) for sentence, score, _ in ranked]


def _stable_key(*values: str) -> str:
    payload = "|".join(values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


class ConsolidationResult(BaseModel):
    """Auditable result of one memory consolidation operation."""

    status: str
    canonical_memory: Memory
    fact_count: int = 0
    duplicate_of: str | None = None
    superseded_memory_ids: list[str] = Field(default_factory=list)
    conflict_detected: bool = False
    algorithm: str = FactExtractionPolicy().algorithm
    algorithm_version: str = FactExtractionPolicy().version


class MemoryConsolidator:
    """Extract, deduplicate and revise semantic memories in the background."""

    def __init__(
        self,
        extractor: FactExtractor | None = None,
        *,
        policy: FactExtractionPolicy | None = None,
        max_peer_candidates: int = 200,
    ) -> None:
        self.policy = policy or FactExtractionPolicy()
        self.extractor = extractor or StructuredFactExtractor(self.policy)
        if max_peer_candidates <= 0:
            raise ValueError("max_peer_candidates must be positive")
        self.max_peer_candidates = max_peer_candidates

    async def consolidate(
        self,
        memory: Memory,
        *,
        manager: MemoryManager,
    ) -> ConsolidationResult:
        enriched = self._attach_facts(memory)
        await manager.write(enriched)
        facts = enriched.facts
        if enriched.type != MemoryType.SEMANTIC or not facts:
            return ConsolidationResult(
                status="extracted" if facts else "no_candidate",
                canonical_memory=enriched,
                fact_count=len(facts),
                algorithm=self.policy.algorithm,
                algorithm_version=self.policy.version,
            )

        peers = await manager.query(
            session_id=enriched.session_id,
            agent_id=enriched.agent_id,
            user_id=enriched.user_id,
            tenant_id=enriched.tenant_id,
            state=MemoryState.ACTIVE,
            limit=self.max_peer_candidates,
        )
        peers = [
            peer
            for peer in peers
            if peer.id != enriched.id and peer.type == MemoryType.SEMANTIC
        ]
        exact = self._matching_peers(enriched, peers, exact=True)
        if exact:
            canonical = max(exact, key=lambda item: (item.revision, item.updated_at, item.id))
            merged = self._merge_duplicate(canonical, enriched)
            await manager.write(merged)
            duplicate_memory = await manager.update_state(enriched.id, MemoryState.SUPERSEDED)
            duplicate_memory = duplicate_memory.model_copy(
                update={
                    "superseded_by": canonical.id,
                    "consolidation_status": "duplicate",
                    "metadata": {
                        **duplicate_memory.metadata,
                        "duplicate_of": canonical.id,
                        "consolidation_algorithm": self.policy.algorithm,
                    },
                }
            )
            await manager.write(duplicate_memory)
            return ConsolidationResult(
                status="merged_duplicate",
                canonical_memory=merged,
                fact_count=len(merged.facts),
                duplicate_of=canonical.id,
                superseded_memory_ids=[enriched.id],
                algorithm=self.policy.algorithm,
                algorithm_version=self.policy.version,
            )

        conflicts = self._matching_peers(enriched, peers, exact=False)
        if conflicts:
            next_revision = max([peer.revision for peer in conflicts] + [enriched.revision]) + 1
            revised_facts = [fact.model_copy(update={"revision": next_revision}) for fact in facts]
            enriched = enriched.model_copy(
                update={
                    "facts": revised_facts,
                    "revision": next_revision,
                    "consolidation_status": "new_revision",
                    "metadata": {
                        **enriched.metadata,
                        "consolidation_algorithm": self.policy.algorithm,
                        "conflict_with": [peer.id for peer in conflicts],
                    },
                }
            )
            superseded_ids: list[str] = []
            for peer in conflicts:
                await manager.update_state(peer.id, MemoryState.SUPERSEDED)
                updated_peer = await manager.get(peer.id)
                if updated_peer is None:
                    continue
                updated_peer = updated_peer.model_copy(
                    update={
                        "superseded_by": enriched.id,
                        "consolidation_status": "superseded_by_revision",
                        "metadata": {
                            **updated_peer.metadata,
                            "superseded_by_revision": next_revision,
                        },
                    }
                )
                await manager.write(updated_peer)
                superseded_ids.append(peer.id)
            await manager.write(enriched)
            return ConsolidationResult(
                status="conflict_revised",
                canonical_memory=enriched,
                fact_count=len(enriched.facts),
                superseded_memory_ids=superseded_ids,
                conflict_detected=True,
                algorithm=self.policy.algorithm,
                algorithm_version=self.policy.version,
            )

        enriched = enriched.model_copy(
            update={
                "consolidation_status": "active",
                "metadata": {
                    **enriched.metadata,
                    "consolidation_algorithm": self.policy.algorithm,
                },
            }
        )
        await manager.write(enriched)
        return ConsolidationResult(
            status="created",
            canonical_memory=enriched,
            fact_count=len(enriched.facts),
            algorithm=self.policy.algorithm,
            algorithm_version=self.policy.version,
        )

    def _attach_facts(self, memory: Memory) -> Memory:
        facts = self.extractor.extract(memory)
        return memory.model_copy(
            update={
                "facts": facts,
                "fact_bundle_version": self.policy.version,
                "consolidation_status": "extracted" if facts else "no_candidate",
                "metadata": {
                    **memory.metadata,
                    "fact_count": len(facts),
                    "fact_algorithm": self.policy.algorithm,
                    "fact_algorithm_version": self.policy.version,
                },
            }
        )

    @staticmethod
    def _matching_peers(
        candidate: Memory,
        peers: list[Memory],
        *,
        exact: bool,
    ) -> list[Memory]:
        candidate_keys = {
            fact.normalized_key if exact else fact.group_key for fact in candidate.facts
        }
        return [
            peer
            for peer in peers
            if any(
                (fact.normalized_key if exact else fact.group_key) in candidate_keys
                for fact in peer.facts
            )
            and (
                not exact
                or any(
                    fact.normalized_key in candidate_keys
                    for fact in peer.facts
                )
            )
            and (
                exact
                or any(
                    fact.normalized_key
                    not in {candidate_fact.normalized_key for candidate_fact in candidate.facts}
                    for fact in peer.facts
                )
            )
        ]

    @staticmethod
    def _merge_duplicate(canonical: Memory, incoming: Memory) -> Memory:
        by_key: dict[str, MemoryFact] = {fact.normalized_key: fact for fact in canonical.facts}
        existing_refs = canonical.metadata.get("evidence_refs", [])
        merged_refs = (
            [str(ref) for ref in existing_refs]
            if isinstance(existing_refs, Sequence) and not isinstance(existing_refs, (str, bytes))
            else []
        )
        existing_merged_ids = canonical.metadata.get("merged_memory_ids", [])
        merged_ids = (
            [str(item) for item in existing_merged_ids]
            if isinstance(existing_merged_ids, Sequence)
            and not isinstance(existing_merged_ids, (str, bytes))
            else []
        )
        for fact in incoming.facts:
            existing = by_key.get(fact.normalized_key)
            if existing is None:
                by_key[fact.normalized_key] = fact
            else:
                merged_evidence = list(
                    dict.fromkeys(existing.evidence_refs + fact.evidence_refs)
                )
                by_key[fact.normalized_key] = existing.model_copy(
                    update={"evidence_refs": merged_evidence}
                )
        merged_refs.extend(ref for fact in incoming.facts for ref in fact.evidence_refs)
        return canonical.model_copy(
            update={
                "facts": list(by_key.values()),
                "consolidation_status": "merged",
                "consolidated_from": list(
                    dict.fromkeys(canonical.consolidated_from + [incoming.id])
                ),
                "metadata": {
                    **canonical.metadata,
                    "evidence_refs": list(dict.fromkeys(str(ref) for ref in merged_refs)),
                    "merged_memory_ids": list(
                        dict.fromkeys(
                            [
                                *merged_ids,
                                incoming.id,
                            ]
                        )
                    ),
                },
            }
        )
