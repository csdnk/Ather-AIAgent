"""Official LLMLingua-2 token deletion with deterministic original provenance.

This is a model-input view, never an authority body or a final memory. Every
returned word (kept AND dropped) is aligned in order so repeated words cannot
silently move evidence to a different occurrence. Characters omitted by the
tokenizer are retained conservatively. Unalignable output fails explicitly.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from pydantic import Field

from aether_agent_memory.remember.basic.extraction import evidence_spans
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.runtime.contracts.models import ContractModel
from aether_agent_memory.runtime.foundation.requests import text_hash


class CompressionView(ContractModel):
    text: str
    # Each model-view character points to its original character. A separator
    # inserted across a deleted run points to the following retained character.
    offsets: tuple[int, ...]
    source_hash: str
    source_chars: int
    original_bytes: int
    retained_bytes: int
    version: int = Field(default=1, ge=1, le=1)

    def validate_source(self, original: str) -> None:
        if (
            text_hash(original) != self.source_hash
            or len(original) != self.source_chars
            or len(self.text) != len(self.offsets)
            or any(index < 0 or index >= len(original) for index in self.offsets)
            or tuple(sorted(self.offsets)) != self.offsets
            or any(
                char != original[index] and char != " "
                for char, index in zip(self.text, self.offsets, strict=True)
            )
            or self.original_bytes != len(original.encode("utf-8"))
            or self.retained_bytes != len(self.text.encode("utf-8"))
        ):
            raise ValueError("LLMLingua view does not match its original source")

    def evidence(self, original: str, quote: str) -> tuple[tuple[int, int, str], ...]:
        self.validate_source(original)
        result = set()
        for start, end, _ in evidence_spans(self.text, quote):
            begin, finish = self.offsets[start], self.offsets[end - 1] + 1
            result.add((begin, finish, original[begin:finish]))
        return tuple(sorted(result))


def labeled_view(original: str, words: list[tuple[str, int]]) -> CompressionView:
    keep = [True] * len(original)
    cursor = 0
    for word, label in words:
        if not word or word == "[UNK]" or label not in {0, 1}:
            raise ValueError("cannot align LLMLingua word labels to original")
        start = original.find(word, cursor)
        if start < 0:
            raise ValueError("cannot align LLMLingua word labels to original")
        # Skipped punctuation/whitespace is normal tokenizer behavior, but an
        # unaccounted lexical word could shift a repeated token to the wrong copy.
        if any(char.isalnum() for char in original[cursor:start]):
            raise ValueError("cannot align LLMLingua labels across unaccounted source words")
        if not label:
            keep[start : start + len(word)] = [False] * len(word)
        cursor = start + len(word)
    if not words or any(char.isalnum() for char in original[cursor:]):
        raise ValueError("cannot align incomplete LLMLingua labels to original")
    # Remove spaces adjacent to removed words, then add one boundary separator.
    # Newlines are not removed: source paragraph and sentence structure matters.
    for index, retained in enumerate(tuple(keep)):
        if not retained:
            for direction in (-1, 1):
                neighbor = index + direction
                while 0 <= neighbor < len(original) and original[neighbor] in " \t":
                    keep[neighbor] = False
                    neighbor += direction
    chars, offsets = [], []
    previous = -1
    for index, retained in enumerate(keep):
        if not retained:
            continue
        if (
            previous >= 0
            and index > previous + 1
            and not original[previous].isspace()
            and not original[index].isspace()
        ):
            chars.append(" ")
            offsets.append(index)
        chars.append(original[index])
        offsets.append(index)
        previous = index
    text = "".join(chars)
    if not text.strip():
        raise ValueError("LLMLingua removed all source content")
    return CompressionView(
        text=text,
        offsets=tuple(offsets),
        source_hash=text_hash(original),
        source_chars=len(original),
        original_bytes=len(original.encode("utf-8")),
        retained_bytes=len(text.encode("utf-8")),
    )


def should_precompress(policy: RememberPolicy, original: str) -> bool:
    return (
        policy.long_memory_route == "llmlingua"
        and len(original.encode("utf-8")) >= policy.compression_min_bytes
    )


class LLMLinguaPreprocessor:
    def __init__(self, policy: RememberPolicy, *, compressor: Any = None) -> None:
        self.policy = policy
        self.compressor = compressor
        self._lock = threading.Lock()

    @property
    def model_identity(self) -> dict[str, Any]:
        return {
            "adapter": "llmlingua2_original_mapping_v1",
            "package": "llmlingua==0.2.2",
            "model": self.policy.llmlingua_model,
            "device": "cpu",
            "revision": self.policy.llmlingua_model_revision,
            "keep_rate": self.policy.llmlingua_keep_rate,
        }

    def checkpoint_identity(self) -> dict[str, Any]:
        return self.model_identity

    def compress(self, original: str) -> CompressionView:
        with self._lock:
            if self.compressor is None:
                try:
                    from llmlingua import PromptCompressor
                except ImportError as exc:
                    raise RuntimeError(
                        "LLMLingua route requires remember-llmlingua dependencies"
                    ) from exc
                self.compressor = PromptCompressor(
                    model_name=self.policy.llmlingua_model,
                    device_map="cpu",
                    use_llmlingua2=True,
                    model_config={
                        "revision": self.policy.llmlingua_model_revision,
                        "trust_remote_code": False,
                    },
                    llmlingua2_config={"max_batch_size": 4},
                )
            word_sep, label_sep = "\u241eLLMLINGUA_WORD\u241e", "\u241fLABEL\u241f"
            if word_sep in original or label_sep in original:
                raise ValueError("source contains reserved LLMLingua label delimiters")
            output = self.compressor.compress_prompt(
                original,
                rate=self.policy.llmlingua_keep_rate,
                return_word_label=True,
                word_sep=word_sep,
                label_sep=label_sep,
                force_reserve_digit=True,
                drop_consecutive=False,
                force_tokens=[
                    "\n",
                    ".",
                    ",",
                    ":",
                    ";",
                    "!",
                    "?",
                    "。",
                    "，",
                    "：",
                    "；",
                    "！",
                    "？",
                    "不",
                    "未",
                    "无",
                    "否",
                    "仅",
                    "如果",
                    "除非",
                    "必须",
                    "not",
                    "no",
                    "unless",
                    "if",
                    "only",
                    "must",
                ],
            )
            labeled = output.get("fn_labeled_original_prompt")
            if not isinstance(labeled, str):
                raise ValueError("LLMLingua did not return required original word labels")
            words = []
            for row in labeled.split(word_sep):
                word, separator, label = row.rpartition(label_sep)
                if not separator or label not in {"0", "1"}:
                    raise ValueError("invalid LLMLingua word label output")
                words.append((word, int(label)))
            return labeled_view(original, words)

    async def acompress(self, original: str) -> CompressionView:
        return await asyncio.to_thread(self.compress, original)
