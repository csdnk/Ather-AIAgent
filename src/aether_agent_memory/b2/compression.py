"""Auditable B2 long-term memory compression.

The implementation is a dependency-light industrial compression pipeline.  It
uses exact duplicate removal, structured-marker protection, sentence-level
selection and an exact UTF-8 byte packer.  The Artifact/persistence contract is
kept stable so the result remains replayable and auditable without a model
classifier or a GPU runtime.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field

from aether_agent_memory.core.memory import Memory

if TYPE_CHECKING:
    from aether_agent_memory.b2.compression_store import CompressionArtifactStore

try:  # pragma: no cover - jieba is an optional runtime fallback
    import jieba
except ImportError:  # pragma: no cover
    jieba = None


_TOKEN_PATTERN = re.compile(
    r"https?://[^\s]+|[A-Za-z]+(?:[-'][A-Za-z]+)*|\d+(?:[.,:/-]\d+)*|"
    r"[\u4e00-\u9fff]+|[^\w\s]",
    re.UNICODE,
)
_SENTENCE_PATTERN = re.compile(r".+?(?:[。！？!?；;\n]+|$)", re.S)
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_NUMBER_PATTERN = re.compile(r"\d")
_RIGHT_PUNCTUATION = set("，。！？；：,.!?;:、)]}》」』”’")
_LEFT_PUNCTUATION = set("([{《「『“‘")


@dataclass(frozen=True)
class CompressionPolicy:
    """Runtime policy for the B2-HSC deterministic compressor."""

    target_ratio: float = 5.0
    min_input_tokens: int = 96
    min_output_tokens: int = 16
    algorithm: str = "b2-hsc-selective-context"
    algorithm_version: str = "1.1"
    keep_boundary_sentences: bool = True
    max_full_sentence_ratio: float = 0.40
    deduplicate_exact_units: bool = True
    protected_terms: tuple[str, ...] = (
        "记住",
        "偏好",
        "喜欢",
        "必须",
        "不能",
        "不得",
        "不要",
        "禁止",
        "未",
        "无",
        "要求",
        "合同",
        "证据",
        "来源",
        "引用",
        "结论",
        "风险",
        "截止",
        "失败",
        "成功",
        "not",
        "never",
        "without",
    )

    def __post_init__(self) -> None:
        if self.target_ratio <= 1.0:
            raise ValueError("target_ratio must be greater than 1")
        if self.min_input_tokens <= 0 or self.min_output_tokens <= 0:
            raise ValueError("token thresholds must be positive")
        if not 0.0 < self.max_full_sentence_ratio <= 1.0:
            raise ValueError("max_full_sentence_ratio must be in (0, 1]")


class CompressionArtifact(BaseModel):
    """Persisted result and audit record for one source memory."""

    artifact_id: str
    source_memory_id: str
    source_id: str | None = None
    algorithm: str
    algorithm_version: str
    compressed_text: str
    original_utf8_bytes: int
    compressed_utf8_bytes: int
    compression_ratio: float
    compression_rate: float
    target_ratio: float
    original_token_count: int
    compressed_token_count: int
    original_sha256: str
    compressed_sha256: str
    status: str
    quality_status: str
    preserved_markers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class _Token:
    text: str
    sentence_index: int
    position: int
    kind: str


def _split_sentences(text: str) -> list[str]:
    parts = [part for part in _SENTENCE_PATTERN.findall(text) if part.strip()]
    return parts or ([text] if text else [])


def _tokenize_sentence(sentence: str, sentence_index: int, start_position: int) -> list[_Token]:
    tokens: list[_Token] = []
    position = start_position
    for match in _TOKEN_PATTERN.finditer(sentence):
        value = match.group(0)
        if _CJK_PATTERN.search(value) and not _NUMBER_PATTERN.search(value):
            if jieba is not None:
                words = list(jieba.tokenize(value, mode="default"))
            else:  # pragma: no cover - exercised only without the optional package
                words = [(char, index, index + 1) for index, char in enumerate(value)]
            for word, _, _ in words:
                tokens.append(_Token(word, sentence_index, position, "cjk"))
                position += 1
            continue
        if value.startswith("http://") or value.startswith("https://"):
            kind = "url"
        elif _NUMBER_PATTERN.search(value):
            kind = "number"
        elif value.isalnum() or value.replace("-", "").replace("'", "").isalnum():
            kind = "word"
        else:
            kind = "punctuation"
        tokens.append(_Token(value, sentence_index, position, kind))
        position += 1
    return tokens


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    for sentence_index, sentence in enumerate(_split_sentences(text)):
        sentence_tokens = _tokenize_sentence(sentence, sentence_index, position)
        tokens.extend(sentence_tokens)
        position += len(sentence_tokens)
    return tokens


def _is_cjk(value: str) -> bool:
    return bool(_CJK_PATTERN.search(value))


def _is_ascii_word(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value))


def _join_tokens(tokens: list[_Token]) -> str:
    parts: list[str] = []
    previous = ""
    for token in tokens:
        value = token.text
        if not parts or (
            value in _RIGHT_PUNCTUATION
            or previous in _LEFT_PUNCTUATION
            or _is_cjk(previous)
            or _is_cjk(value)
        ):
            parts.append(value)
        elif _is_ascii_word(previous) and _is_ascii_word(value):
            parts.extend((" ", value))
        else:
            parts.append(value)
        previous = value
    return "".join(parts).strip()


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _deduplicate_sentences(
    text: str,
    *,
    protected_terms: tuple[str, ...],
    keyword_terms: tuple[str, ...],
) -> tuple[str, int]:
    """Remove exact repeated non-factual sentences before ranking.

    Exact duplicate removal is deliberately conservative: sentences carrying
    a protected marker, number or URL are retained because repetitions may be
    separate pieces of evidence.  Boilerplate and repeated conversational
    scaffolding are safe to collapse to their first occurrence.
    """

    sentences = _split_sentences(text)
    if len(sentences) < 2:
        return text, 0
    seen: set[str] = set()
    kept: list[str] = []
    removed = 0
    markers = (*protected_terms, *keyword_terms)
    for sentence in sentences:
        normalized = _normalized(sentence)
        if not normalized:
            continue
        has_marker = any(
            _normalized(marker) in normalized for marker in markers if marker.strip()
        )
        has_structured_value = bool(_NUMBER_PATTERN.search(sentence)) or (
            "http://" in sentence.casefold() or "https://" in sentence.casefold()
        )
        if normalized in seen and not has_marker and not has_structured_value:
            removed += 1
            continue
        seen.add(normalized)
        kept.append(sentence)
    if not removed:
        return text, 0
    return "".join(kept), removed


class HybridMemoryCompressor:
    """B2-HSC v1.1: industrial record selection plus exact byte packing."""

    def __init__(self, policy: CompressionPolicy | None = None) -> None:
        self.policy = policy or CompressionPolicy()

    def should_compress(self, text: str) -> bool:
        """Return whether the text crosses the policy's long-memory threshold."""
        return len(_tokenize(text.strip())) >= self.policy.min_input_tokens

    def compress(
        self,
        text: str,
        *,
        source_memory_id: str,
        source_id: str | None = None,
        keywords: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CompressionArtifact:
        original_text = text if text.strip() else ""
        original_bytes = len(original_text.encode("utf-8"))
        original_sha256 = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
        original_tokens = _tokenize(original_text)
        original_token_count = len(original_tokens)
        artifact_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{source_memory_id}:{original_sha256}:{self.policy.algorithm_version}",
            )
        )
        if not original_text:
            return self._artifact(
                artifact_id=artifact_id,
                source_memory_id=source_memory_id,
                source_id=source_id,
                original_text=original_text,
                compressed_text="",
                original_token_count=0,
                status="empty",
                preserved_markers=[],
                warnings=["empty source text"],
                metadata=metadata,
            )
        if original_token_count < self.policy.min_input_tokens:
            return self._artifact(
                artifact_id=artifact_id,
                source_memory_id=source_memory_id,
                source_id=source_id,
                original_text=original_text,
                compressed_text=original_text,
                original_token_count=original_token_count,
                status="skipped",
                preserved_markers=self._markers_in(original_text, keywords or []),
                warnings=["input below min_input_tokens"],
                metadata=metadata,
            )

        keyword_terms = tuple(term for term in (keywords or []) if term.strip())
        working_text = original_text
        deduplicated_units = 0
        if self.policy.deduplicate_exact_units:
            working_text, deduplicated_units = _deduplicate_sentences(
                original_text,
                protected_terms=self.policy.protected_terms,
                keyword_terms=keyword_terms,
            )
        tokens = _tokenize(working_text)
        if not tokens:
            working_text = original_text
            tokens = original_tokens

        target_tokens = min(
            original_token_count,
            max(
                self.policy.min_output_tokens,
                math.ceil(original_token_count / self.policy.target_ratio),
            ),
        )
        target_bytes = max(1, math.floor(original_bytes / self.policy.target_ratio))
        sentence_tokens: dict[int, list[_Token]] = {}
        for token in tokens:
            sentence_tokens.setdefault(token.sentence_index, []).append(token)
        sentence_texts = _split_sentences(working_text)
        frequencies = Counter(
            token.text.casefold()
            for token in tokens
            if token.kind != "punctuation" and len(token.text.strip()) > 1
        )
        full_sentence_limit = max(
            1,
            math.ceil(len(sentence_tokens) * self.policy.max_full_sentence_ratio),
        )
        full_sentences: set[int] = set()
        sentence_scores: dict[int, float] = {}
        for index, sentence in enumerate(sentence_texts):
            group = sentence_tokens.get(index, [])
            score = self._sentence_score(sentence, group, keyword_terms, index, len(sentence_texts))
            sentence_scores[index] = score
            if self._is_high_confidence_fact(sentence, group, keyword_terms):
                full_sentences.add(index)
        if len(full_sentences) > full_sentence_limit:
            ranked = sorted(full_sentences, key=lambda index: sentence_scores[index], reverse=True)
            full_sentences = set(ranked[:full_sentence_limit])

        selected_positions: set[int] = set()
        hard_protected_positions: set[int] = set()
        full_sentence_positions: set[int] = set()
        for token in tokens:
            if token.sentence_index in full_sentences:
                full_sentence_positions.add(token.position)
            if self._is_protected_token(token, keyword_terms):
                hard_protected_positions.add(token.position)
        selected_positions.update(full_sentence_positions)
        selected_positions.update(hard_protected_positions)

        # Prefer complete sentence units before token-level filling.  This is
        # the stable enterprise path: it keeps summaries readable and uses
        # token pruning only for the final byte-budget adjustment.
        sentence_candidates = [
            (
                sentence_scores[index]
                / max(len(sentence.encode("utf-8")), 1),
                sentence_scores[index],
                index,
            )
            for index, sentence in enumerate(sentence_texts)
            if index not in full_sentences and sentence_tokens.get(index)
        ]
        sentence_candidates.sort(reverse=True)
        selected_sentence_indexes = set(full_sentences)
        current_text = _join_tokens(
            [token for token in tokens if token.position in selected_positions]
        )
        current_bytes = len(current_text.encode("utf-8"))
        for _, _, index in sentence_candidates:
            if current_bytes >= target_bytes:
                break
            group = sentence_tokens[index]
            group_positions = {token.position for token in group}
            sentence_bytes = len(sentence_texts[index].encode("utf-8"))
            estimated_increment = sentence_bytes + (1 if current_text else 0)
            new_token_count = len(selected_positions) + sum(
                position not in selected_positions for position in group_positions
            )
            if (
                current_bytes + estimated_increment <= target_bytes
                and new_token_count <= target_tokens
            ):
                selected_positions.update(group_positions)
                selected_sentence_indexes.add(index)
                current_bytes += estimated_increment
                current_text = current_text if current_text else sentence_texts[index]

        candidates = [token for token in tokens if token.position not in selected_positions]
        token_scores = {
            token.position: self._token_score(
                token,
                frequencies=frequencies,
                keyword_terms=keyword_terms,
                sentence_count=len(sentence_texts),
                sentence_size=len(sentence_tokens.get(token.sentence_index, [])),
            )
            for token in tokens
        }
        candidates.sort(
            key=lambda token: (
                token_scores[token.position]
                / max(len(token.text.encode("utf-8")), 1),
                token_scores[token.position],
            ),
            reverse=True,
        )
        current_text = _join_tokens(
            [token for token in tokens if token.position in selected_positions]
        )
        current_bytes = len(current_text.encode("utf-8"))
        for token in candidates:
            if len(selected_positions) >= target_tokens or current_bytes >= target_bytes:
                break
            # The exact byte count is recalculated once after selection.  The
            # conservative one-byte join allowance keeps long documents out
            # of an O(tokens²) trial-render loop.
            estimated_increment = len(token.text.encode("utf-8")) + 1
            if current_bytes + estimated_increment <= target_bytes:
                selected_positions.add(token.position)
                current_bytes += estimated_increment

        # Keep terminal punctuation for selected sentences without replacing protected facts.
        for token in tokens:
            if token.position in selected_positions or token.text not in _RIGHT_PUNCTUATION:
                continue
            same_sentence = [
                item
                for item in sentence_tokens[token.sentence_index]
                if item.position < token.position
            ]
            if any(item.position in selected_positions for item in same_sentence[-3:]) and (
                current_bytes + len(token.text.encode("utf-8")) <= target_bytes
            ):
                selected_positions.add(token.position)
                current_bytes += len(token.text.encode("utf-8"))

        selected_text = _join_tokens(
            [token for token in tokens if token.position in selected_positions]
        )
        current_bytes = len(selected_text.encode("utf-8"))
        # If selected units overshoot the byte target, remove the lowest-value
        # non-protected tokens with a binary search over exact rendered bytes.
        # This avoids the old approximate ``len(token) + 1`` accounting drift.
        trimmed_token_count = 0
        if current_bytes > target_bytes:
            selected_positions, trimmed_token_count = self._trim_to_byte_budget(
                tokens,
                selected_positions=selected_positions,
                hard_protected_positions=hard_protected_positions,
                token_scores=token_scores,
                target_bytes=target_bytes,
            )

        selected_tokens = [token for token in tokens if token.position in selected_positions]
        compressed_text = _join_tokens(selected_tokens)
        if not compressed_text:
            compressed_text = sentence_texts[0].strip() if sentence_texts else original_text
        marker_candidates = [*self.policy.protected_terms, *keyword_terms]
        compressed_markers = self._markers_in(compressed_text, marker_candidates)
        required_markers = self._markers_in(original_text, marker_candidates)
        missing_markers = [
            marker for marker in required_markers if marker not in compressed_markers
        ]
        warnings: list[str] = []
        if missing_markers:
            warnings.append(f"protected markers missing: {', '.join(missing_markers)}")
        compressed_token_count = len(_tokenize(compressed_text))
        status = "compressed"
        provisional_ratio = original_bytes / max(len(compressed_text.encode("utf-8")), 1)
        if provisional_ratio < self.policy.target_ratio:
            status = "below_target"
            warnings.append(
                f"ratio {provisional_ratio:.3f} below target {self.policy.target_ratio:.3f}"
            )
        if compressed_text == original_text:
            status = "below_target"
            warnings.append("compressor produced no reduction")
        quality_status = "protected_markers_retained" if not missing_markers else "review_required"
        return self._artifact(
            artifact_id=artifact_id,
            source_memory_id=source_memory_id,
            source_id=source_id,
            original_text=original_text,
            compressed_text=compressed_text,
            original_token_count=original_token_count,
            compressed_token_count=compressed_token_count,
            status=status,
            quality_status=quality_status,
            preserved_markers=compressed_markers,
            warnings=warnings,
            metadata={
                **(metadata or {}),
                "target_tokens": target_tokens,
                "target_compressed_bytes": target_bytes,
                "preprocessing": "exact_sentence_dedup_and_record_selection",
                "deduplicate_exact_units": self.policy.deduplicate_exact_units,
                "deduplicated_sentence_count": deduplicated_units,
                "preprocessed_utf8_bytes": len(working_text.encode("utf-8")),
                "selected_sentence_count": len(selected_sentence_indexes),
                "candidate_sentence_count": len(sentence_candidates),
                "trimmed_token_count": trimmed_token_count,
                "protected_token_count": len(hard_protected_positions),
                "selected_token_count": len(selected_tokens),
            },
        )
    async def compress_and_store(
        self,
        text: str,
        *,
        source_memory_id: str,
        store: CompressionArtifactStore,
        source_id: str | None = None,
        keywords: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CompressionArtifact:
        artifact = self.compress(
            text,
            source_memory_id=source_memory_id,
            source_id=source_id,
            keywords=keywords,
            metadata=metadata,
        )
        await store.put(artifact)
        return artifact

    def _sentence_score(
        self,
        sentence: str,
        tokens: list[_Token],
        keyword_terms: tuple[str, ...],
        index: int,
        sentence_count: int,
    ) -> float:
        score = 0.0
        normalized = sentence.casefold()
        score += sum(6.0 for term in self.policy.protected_terms if term.casefold() in normalized)
        score += sum(5.0 for term in keyword_terms if term.casefold() in normalized)
        score += sum(3.0 for token in tokens if token.kind in {"number", "url"})
        if self.policy.keep_boundary_sentences and (
            index == 0 or index == sentence_count - 1
        ):
            score += 2.0
        return score

    def _is_high_confidence_fact(
        self,
        sentence: str,
        tokens: list[_Token],
        keyword_terms: tuple[str, ...],
    ) -> bool:
        normalized = sentence.casefold()
        marker = any(term.casefold() in normalized for term in self.policy.protected_terms)
        keyword = any(term.casefold() in normalized for term in keyword_terms)
        numeric = sum(1 for token in tokens if token.kind in {"number", "url"}) >= 2
        return marker or keyword or numeric

    def _is_protected_token(self, token: _Token, keyword_terms: tuple[str, ...]) -> bool:
        value = token.text.casefold()
        return (
            token.kind in {"number", "url"}
            or any(term.casefold() in value for term in self.policy.protected_terms)
            or any(term.casefold() in value for term in keyword_terms)
        )

    def _token_score(
        self,
        token: _Token,
        *,
        frequencies: Counter[str],
        keyword_terms: tuple[str, ...],
        sentence_count: int,
        sentence_size: int,
    ) -> float:
        value = token.text.casefold()
        if token.kind == "punctuation":
            return 0.1
        score = 1.0
        score += 2.0 if token.kind in {"number", "url"} else 0.0
        score += 3.0 if any(term.casefold() in value for term in keyword_terms) else 0.0
        score += 1.5 if len(token.text) > 1 else 0.0
        score += 1.0 / max(frequencies.get(value, 1), 1)
        score += 0.5 if token.sentence_index in {0, sentence_count - 1} else 0.0
        score += 0.25 / max(sentence_size, 1)
        return score

    @staticmethod
    def _trim_to_byte_budget(
        tokens: list[_Token],
        *,
        selected_positions: set[int],
        hard_protected_positions: set[int],
        token_scores: dict[int, float],
        target_bytes: int,
    ) -> tuple[set[int], int]:
        """Trim selected content using exact UTF-8 byte measurements.

        The removable tokens are ordered from least to most valuable.  A
        binary search finds the smallest prefix that satisfies the byte gate,
        so long records do not pay an O(tokens²) render cost.
        """

        removable = [
            token
            for token in tokens
            if token.position in selected_positions
            and token.position not in hard_protected_positions
        ]
        removable.sort(
            key=lambda token: (
                token_scores.get(token.position, 0.0)
                / max(len(token.text.encode("utf-8")), 1),
                token_scores.get(token.position, 0.0),
            )
        )
        if not removable:
            return selected_positions, 0

        def rendered_bytes(remove_count: int) -> int:
            removed = {token.position for token in removable[:remove_count]}
            rendered = _join_tokens(
                [
                    token
                    for token in tokens
                    if token.position in selected_positions and token.position not in removed
                ]
            )
            return len(rendered.encode("utf-8"))

        if rendered_bytes(len(removable)) > target_bytes:
            return selected_positions, 0

        low = 0
        high = len(removable)
        while low < high:
            middle = (low + high) // 2
            if rendered_bytes(middle) <= target_bytes:
                high = middle
            else:
                low = middle + 1
        selected_positions = set(selected_positions)
        selected_positions.difference_update(
            token.position for token in removable[:low]
        )
        return selected_positions, low

    @staticmethod
    def _markers_in(text: str, candidates: list[str]) -> list[str]:
        normalized_text = _normalized(text)
        unique: list[str] = []
        for candidate in candidates:
            marker = _normalized(candidate)
            if marker and marker in normalized_text and candidate not in unique:
                unique.append(candidate)
        return unique

    def _artifact(
        self,
        *,
        artifact_id: str,
        source_memory_id: str,
        source_id: str | None,
        original_text: str,
        compressed_text: str,
        original_token_count: int,
        status: str,
        preserved_markers: list[str],
        warnings: list[str],
        metadata: dict[str, Any] | None,
        compressed_token_count: int | None = None,
        quality_status: str = "not_evaluated",
    ) -> CompressionArtifact:
        original_bytes = len(original_text.encode("utf-8"))
        compressed_bytes = len(compressed_text.encode("utf-8"))
        ratio = original_bytes / max(compressed_bytes, 1) if original_bytes else 1.0
        compressed_sha256 = hashlib.sha256(compressed_text.encode("utf-8")).hexdigest()
        return CompressionArtifact(
            artifact_id=artifact_id,
            source_memory_id=source_memory_id,
            source_id=source_id,
            algorithm=self.policy.algorithm,
            algorithm_version=self.policy.algorithm_version,
            compressed_text=compressed_text,
            original_utf8_bytes=original_bytes,
            compressed_utf8_bytes=compressed_bytes,
            compression_ratio=round(ratio, 6),
            compression_rate=round(1.0 - compressed_bytes / max(original_bytes, 1), 6),
            target_ratio=self.policy.target_ratio,
            original_token_count=original_token_count,
            compressed_token_count=compressed_token_count
            if compressed_token_count is not None
            else len(_tokenize(compressed_text)),
            original_sha256=hashlib.sha256(original_text.encode("utf-8")).hexdigest(),
            compressed_sha256=compressed_sha256,
            status=status,
            quality_status=quality_status,
            preserved_markers=preserved_markers,
            warnings=warnings,
            metadata={"scorer": "deterministic-rule", **(metadata or {})},
        )


def attach_compression_metadata(memory: Memory, artifact: CompressionArtifact) -> Memory:
    """Attach the auditable compression projection to a primary B2 Memory."""
    return memory.model_copy(
        update={
            "compression_artifact_id": artifact.artifact_id,
            "compression_status": artifact.status,
            "compression_ratio": artifact.compression_ratio,
            "metadata": {
                **memory.metadata,
                "compression_artifact_id": artifact.artifact_id,
                "compression_status": artifact.status,
                "compression_ratio": artifact.compression_ratio,
                "compression_rate": artifact.compression_rate,
                "original_utf8_bytes": artifact.original_utf8_bytes,
                "compressed_utf8_bytes": artifact.compressed_utf8_bytes,
                "original_token_count": artifact.original_token_count,
                "compressed_token_count": artifact.compressed_token_count,
                "compression_algorithm": artifact.algorithm,
                "compression_algorithm_version": artifact.algorithm_version,
                "compression_quality_status": artifact.quality_status,
                "compression_warnings": list(artifact.warnings),
            },
        }
    )
