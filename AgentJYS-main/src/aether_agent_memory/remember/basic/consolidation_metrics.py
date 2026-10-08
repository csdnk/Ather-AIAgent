"""Observe original-to-long-term representation size without discarding facts."""

from typing import Any

from aether_agent_memory.remember.contracts.models import MemoryKind, MemorySnapshot


def compression_metrics(
    originals: tuple[MemorySnapshot, ...],
    results: tuple[MemorySnapshot, ...],
    *,
    target_factor: float,
    long_input_bytes: int,
    complete: bool,
    output_sources: dict[str, set[tuple[str, int, str]]] | None = None,
) -> dict[str, Any]:
    """Count source originals and the distinct final bodies returned by a batch.

    Reused existing memories count in full, including retained old qualifications.
    This is representation compression, not newly allocated storage. Source text,
    provenance, indexes and cache replicas are excluded from the output numerator.
    A zero-result or incomplete batch cannot claim a successful compression factor.
    """
    sources: dict[tuple[str, int, str], dict[str, Any]] = {}
    for item in originals:
        for source in item.sources:
            key = (source.source_id, source.source_version, source.content_hash)
            sources.setdefault(
                key,
                {
                    "source": source.model_dump(mode="json"),
                    "bytes": len(item.content.encode("utf-8")),
                },
            )
    # The caller resolves current final bodies after all batch mutations. Keep
    # this deduplication as an additional guard against repeated output refs.
    memories = {
        item.ref.model_dump_json(): {
            "memory": item.ref.model_dump(mode="json"),
            "bytes": len(item.content.encode("utf-8")),
        }
        for item in results
    }

    def measure(original_bytes: int, output_rows: list[dict[str, Any]]) -> dict[str, Any]:
        result_bytes = sum(row["bytes"] for row in output_rows)
        measured = complete and original_bytes > 0 and result_bytes > 0
        factor = original_bytes / result_bytes if measured else None
        return {
            "original_bytes": original_bytes,
            "long_term_bytes": result_bytes,
            "output_over_input": result_bytes / original_bytes if complete and original_bytes else None,
            "compression_factor": factor,
            "target_factor": target_factor,
            "target_met": factor >= target_factor if factor is not None else None,
            "target_enforced": False,
            "status": "incomplete"
            if not complete
            else "empty_input"
            if not original_bytes
            else "no_long_term_output"
            if not result_bytes
            else "measured",
            "result_memory_count": len(output_rows),
            "outputs": output_rows,
        }

    # Use associations produced by THIS commit. A reused memory may also cite
    # unrelated historical sources; those must not attribute new output to them.
    associations = output_sources if output_sources is not None else {
        result.ref.memory_id: {
            (source.source_id, source.source_version, source.content_hash)
            for source in result.sources
        }
        for result in results
    }
    working_inputs: dict[str, dict[str, Any]] = {}
    for item in originals:
        if item.kind != MemoryKind.WORKING:
            continue
        input_keys = {(s.source_id, s.source_version, s.content_hash) for s in item.sources}
        working = working_inputs.setdefault(item.ref.model_dump_json(), {
            "ref": item.ref,
            "source_keys": set(),
        })
        working["source_keys"].update(input_keys)
    per_working = []
    for working in working_inputs.values():
        input_keys = working["source_keys"]
        related_outputs = [
            row for row in memories.values()
            if input_keys.intersection(associations.get(row["memory"]["memory_id"], set()))
        ]
        original_bytes = sum(sources[key]["bytes"] for key in input_keys)
        per_working.append({
            "working_memory": working["ref"].model_dump(mode="json"),
            "scope": "whole_working_memory",
            "all_parts_complete": complete,
            "is_long_input": original_bytes >= long_input_bytes,
            "sources": [sources[key]["source"] for key in sorted(input_keys)],
            **measure(original_bytes, related_outputs),
        })
    return {
        "policy": "original_to_long_term_utf8_v2_whole_working",
        "scope": "consolidation_batch",
        "input_representation": "original",
        "output_representation": "final_long_term_bodies_including_reuse",
        **measure(sum(row["bytes"] for row in sources.values()), list(memories.values())),
        "input_source_count": len(sources),
        "long_input_count": sum(row["bytes"] >= long_input_bytes for row in sources.values()),
        "inputs": list(sources.values()),
        "per_working_memory": per_working,
        # A jointly supported final body is counted once for EACH parent it
        # represents. Per-parent byte totals must not be summed as storage cost.
        "per_working_totals_additive": False,
        "accuracy_evaluated": False,
        "fact_retention_evaluated": False,
    }
