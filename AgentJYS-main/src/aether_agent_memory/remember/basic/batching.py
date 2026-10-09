"""Whole-message admission units, independent from model token windows."""

from typing import Any

from .policy import RememberPolicy


def select_batch(
    rows: list[tuple[str, dict[str, Any]]], policy: RememberPolicy
) -> list[tuple[str, dict[str, Any]]]:
    """Aggregate whole short messages; each long original is scheduled separately."""
    selected = []
    size = 0
    for entry in rows:
        if entry[1]["bytes"] >= policy.compression_min_bytes:
            continue
        selected.append(entry)
        size += entry[1]["bytes"]
        if len(selected) >= policy.consolidation_messages or size >= policy.consolidation_bytes:
            break
    return selected
