"""Northbound v1 compatibility response mappers."""

from __future__ import annotations

from typing import Any

from aether_agent_memory.runtime.dtos import (
    LongMemorySubmission,
    MemorySearchResult,
    TaskStatusRecord,
)


def long_memory_submission_to_v1(submission: LongMemorySubmission) -> dict[str, Any]:
    data = submission.model_dump(mode="json", exclude_none=True)
    if submission.provider == "p2" and submission.namespace:
        data["p2_bucket"] = submission.namespace
    return data


def task_status_record_to_v1(record: TaskStatusRecord) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude_none=True)
    if record.provider == "p2":
        if record.projection_count is not None:
            data["p2_vector_count"] = record.projection_count
        if record.projection_namespace:
            data["p2_collection"] = record.projection_namespace
    return data


def memory_search_result_to_v1(result: MemorySearchResult) -> dict[str, Any]:
    data = result.model_dump(mode="json", exclude_none=True)
    if result.namespace:
        data["collection"] = result.namespace
    return data
