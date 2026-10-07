"""Versioned Remember policy. Capacity limits are not storage-provider maxima."""

from collections.abc import Callable

from pydantic import Field

from aether_agent_memory.runtime.contracts.models import ContractModel, Identifier


class RememberPolicy(ContractModel):
    version: Identifier = "remember_v6"
    working_summary_min_bytes: int = Field(default=65536, ge=1)
    working_summary_max_chars: int = Field(default=2048, ge=256, le=16384)
    summary_part_chars: int = Field(default=256, ge=32, le=2048)
    summary_attempts: int = Field(default=3, ge=1, le=5)
    summary_call_timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    source_page_chars: int = Field(default=4096, ge=256, le=65536)
    source_read_max_chars: int = Field(default=65536, ge=256, le=262144)
    max_input_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    compression_min_bytes: int = Field(default=8000, ge=1)
    compression_target_ratio: float = Field(default=5.0, ge=5, allow_inf_nan=False)
    cache_max_body_bytes: int = Field(default=1024 * 1024, ge=1)
    cache_scope_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    cache_ttl_seconds: int = Field(default=86400, ge=1)
    processing_seconds: int = Field(default=86400, ge=60)
    consolidation_messages: int = Field(default=32, ge=1)
    consolidation_tokens: int = Field(default=8000, ge=1)
    # Tokens remain readable in old task snapshots; new admission uses UTF-8 bytes.
    consolidation_bytes: int = Field(default=8000, ge=1)
    consolidation_seconds: int = Field(default=3600, ge=1)
    consolidation_overlap_messages: int = Field(default=2, ge=0, le=2)
    extraction_chunk_tokens: int = Field(default=4096, ge=16)
    projection_chunk_tokens: int = Field(default=256, ge=8)
    max_candidates: int = Field(default=32, ge=1, le=256)
    comparison_candidates: int = Field(default=8, ge=1, le=100)
    comparison_context_tokens: int = Field(default=32768, ge=256)
    max_commit_retries: int = Field(default=3, ge=1, le=10)
    max_model_calls: int = Field(default=256, ge=1)
    # Observe the target by default; deployments may opt back into strict publication.
    compression_require_ratio: bool = False


def importance(category: str) -> tuple[float, str]:
    """Value to the application, neither truth confidence nor access heat."""
    return {
        "observation": (0.2, "ordinary_observation"),
        "event": (0.5, "durable_event"),
        "fact": (0.5, "durable_fact"),
        "decision": (0.8, "key_decision_or_constraint"),
        "explicit_constraint": (1.0, "explicit_user_constraint"),
    }.get(category, (0.2, "ordinary_observation"))


def chunks(text: str, count: Callable[[str], int], budget: int) -> list[tuple[int, int, str]]:
    """Exact, non-overlapping Unicode ranges, measured by the provider tokenizer.

    No whitespace normalization or byte decoding at token boundaries. Every
    character is covered, including separators; a single oversized character is
    rejected rather than silently dropped.
    """
    result = []
    start = 0
    while start < len(text):
        high = min(len(text), start + budget * 8)
        low = start + 1
        if count(text[start:low]) > budget:
            raise ValueError("one character exceeds tokenizer budget")
        while low < high:
            middle = (low + high + 1) // 2
            if count(text[start:middle]) <= budget:
                low = middle
            else:
                high = middle - 1
        result.append((start, low, text[start:low]))
        start = low
    return result
