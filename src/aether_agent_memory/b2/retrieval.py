"""Enterprise-friendly hybrid memory retrieval.

The retriever combines sparse lexical matching (BM25), dense similarity and
rank fusion (RRF).  A lightweight semantic reranker is the default; a
CrossEncoder can be enabled when the optional ``sentence-transformers``
dependency and a local model are available.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from aether_agent_memory.core.memory import Memory, RecalledMemory

try:  # pragma: no cover - optional tokenizer is available in the project runtime
    import jieba
except ImportError:  # pragma: no cover
    jieba = None


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]|[^\W_]", re.UNICODE)


@dataclass(frozen=True)
class HybridRetrievalPolicy:
    """Weights for the enterprise hybrid ranking pipeline."""

    rrf_k: int = 60
    lexical_weight: float = 1.0
    dense_weight: float = 1.0
    semantic_weight: float = 0.25
    prior_weight: float = 0.15
    importance_weight: float = 0.05

    def __post_init__(self) -> None:
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        for name in (
            "lexical_weight",
            "dense_weight",
            "semantic_weight",
            "prior_weight",
            "importance_weight",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")


class SemanticReranker(Protocol):
    def score(self, query: str, memory: Memory) -> float: ...


def _tokens(text: str) -> list[str]:
    if jieba is not None:
        words = jieba.lcut(text, cut_all=False)
        return [word.casefold() for word in words if word.strip()]
    return [match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(text)]


def _search_text(memory: Memory) -> str:
    fact_text = " ".join(fact.value for fact in memory.facts)
    return f"{memory.content} {fact_text}".strip()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(left * right for left, right in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _minmax(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    low = min(values.values())
    high = max(values.values())
    if math.isclose(low, high):
        return {key: 0.5 for key in values}
    return {key: (value - low) / (high - low) for key, value in values.items()}


def _bm25_scores(query: str, memories: list[Memory]) -> dict[str, float]:
    query_terms = Counter(_tokens(query))
    documents = {memory.id: _tokens(_search_text(memory)) for memory in memories}
    if not query_terms or not documents:
        return {memory.id: 0.0 for memory in memories}
    document_frequency: Counter[str] = Counter()
    for terms in documents.values():
        document_frequency.update(set(terms))
    average_length = sum(len(terms) for terms in documents.values()) / max(len(documents), 1)
    k1 = 1.2
    b = 0.75
    total_documents = len(documents)
    scores: dict[str, float] = {}
    for memory_id, terms in documents.items():
        term_frequency = Counter(terms)
        length_factor = 1.0 - b + b * len(terms) / max(average_length, 1.0)
        score = 0.0
        for term, query_frequency in query_terms.items():
            frequency = term_frequency.get(term, 0)
            if frequency == 0:
                continue
            document_count = document_frequency.get(term, 0)
            inverse_document_frequency = math.log(
                1.0 + (total_documents - document_count + 0.5) / (document_count + 0.5)
            )
            term_score = (
                inverse_document_frequency
                * frequency
                * (k1 + 1.0)
                / (frequency + k1 * length_factor)
            )
            score += term_score * min(query_frequency, 2)
        scores[memory_id] = score
    return scores


class LexicalSemanticReranker:
    """Dependency-light semantic approximation used as the safe default."""

    def score(self, query: str, memory: Memory) -> float:
        query_terms = set(_tokens(query))
        content_terms = set(_tokens(_search_text(memory)))
        if not query_terms or not content_terms:
            return 0.0
        overlap = len(query_terms & content_terms) / len(query_terms)
        phrase_bonus = 0.2 if query.casefold().strip() in _search_text(memory).casefold() else 0.0
        keyword_bonus = 0.0
        metadata_keywords = memory.metadata.get("keywords", [])
        if isinstance(metadata_keywords, list):
            keyword_bonus = 0.1 * sum(
                str(keyword).casefold() in query.casefold() for keyword in metadata_keywords
            )
        return min(1.0, overlap + phrase_bonus + keyword_bonus)


class CrossEncoderSemanticReranker:
    """Optional local CrossEncoder reranker with a deterministic fallback."""

    def __init__(
        self,
        model_name: str,
        *,
        fallback: SemanticReranker | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        self.model_name = model_name
        self._fallback = fallback or LexicalSemanticReranker()
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            module = __import__("sentence_transformers", fromlist=["CrossEncoder"])
            cross_encoder = module.CrossEncoder

            self._model = cross_encoder(self.model_name)
        return self._model

    def score(self, query: str, memory: Memory) -> float:
        try:
            raw = self._load().predict([(query, memory.content)], show_progress_bar=False)
            value = float(raw[0] if isinstance(raw, (list, tuple)) else raw)
            return 1.0 / (1.0 + math.exp(-value))
        except Exception:
            # An unavailable reranker must not block context assembly.
            return self._fallback.score(query, memory)


class HybridMemoryRetriever:
    """BM25 + dense similarity + RRF + semantic reranking."""

    strategy_name = "bm25+dense+rrf+semantic-rerank"

    def __init__(
        self,
        policy: HybridRetrievalPolicy | None = None,
        *,
        reranker: SemanticReranker | None = None,
    ) -> None:
        self.policy = policy or HybridRetrievalPolicy()
        self.reranker = reranker or LexicalSemanticReranker()

    def rank(
        self,
        query: str,
        memories: list[Memory],
        *,
        query_embedding: list[float] | None = None,
        dense_scores: dict[str, float] | None = None,
        prior_scores: dict[str, float] | None = None,
    ) -> list[RecalledMemory]:
        if not memories:
            return []
        lexical_scores = _bm25_scores(query, memories)
        resolved_dense = {
            memory.id: (
                dense_scores[memory.id]
                if dense_scores is not None and memory.id in dense_scores
                else _cosine(query_embedding or [], memory.embedding or [])
            )
            for memory in memories
        }
        lexical_rank = self._rank_positions(lexical_scores)
        dense_rank = self._rank_positions(resolved_dense)
        lexical_rrf = self._rrf(lexical_rank, self.policy.lexical_weight)
        dense_rrf = self._rrf(dense_rank, self.policy.dense_weight)
        normalized_prior = _minmax(prior_scores or {})
        normalized_semantic = {
            memory.id: self.reranker.score(query, memory) for memory in memories
        }
        normalized_semantic = _minmax(normalized_semantic)
        scored: list[RecalledMemory] = []
        for memory in memories:
            score = lexical_rrf.get(memory.id, 0.0) + dense_rrf.get(memory.id, 0.0)
            score += self.policy.semantic_weight * normalized_semantic.get(memory.id, 0.0)
            score += self.policy.prior_weight * normalized_prior.get(memory.id, 0.0)
            score += self.policy.importance_weight * min(max(memory.importance, 0.0), 1.0)
            scored.append(RecalledMemory(memory=memory, score=round(score, 8)))
        scored.sort(
            key=lambda item: (item.score, item.memory.updated_at, item.memory.id),
            reverse=True,
        )
        return scored

    def _rank_positions(self, scores: dict[str, float]) -> dict[str, int]:
        ordered = sorted(scores.items(), key=lambda item: (item[1], item[0]), reverse=True)
        return {memory_id: index for index, (memory_id, _) in enumerate(ordered, start=1)}

    def _rrf(self, positions: dict[str, int], weight: float) -> dict[str, float]:
        return {
            memory_id: weight / (self.policy.rrf_k + position)
            for memory_id, position in positions.items()
        }


__all__ = [
    "CrossEncoderSemanticReranker",
    "HybridMemoryRetriever",
    "HybridRetrievalPolicy",
    "LexicalSemanticReranker",
    "SemanticReranker",
]
