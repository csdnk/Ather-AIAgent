"""Pure scope_union_v1 rules; no health, keyword or cache-based routing."""

from __future__ import annotations

from aether_agent_memory.recall.models import SourceSelection
from aether_agent_memory.runtime.contract_types import MemoryLabel, Scope


def scope_union_v1(
    scope: Scope,
    memory_types: list[MemoryLabel],
    *,
    working_read: bool,
    long_term_read: bool,
) -> SourceSelection:
    """Inputs must already be verified; unknown authority is not False."""
    return SourceSelection(
        strategy="scope_union_v1",
        working_eligibility=(
            "no_current_scope"
            if not (scope.session_id or scope.task_id)
            else "not_authorized"
            if not working_read
            else "type_filtered"
            if "Working" not in memory_types
            else "eligible"
        ),
        long_term_eligibility=(
            "not_authorized"
            if not long_term_read
            else "type_filtered"
            if not {"Episodic", "Semantic"}.intersection(memory_types)
            else "eligible"
        ),
    )
