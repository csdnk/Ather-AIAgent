"""Compatibility wrapper for the P3 HTTP service.

The real application host lives in ``aether_agent_memory.app``.  This module is
kept so existing scripts/tests that import dashboard demo helpers continue to
work during the migration.
"""

from __future__ import annotations

from time import monotonic as monotonic
from typing import Any

from aether_agent_memory.app import main
from aether_agent_memory.demo import service as _demo_service

STATE = _demo_service.STATE
submit_long_text = _demo_service.submit_long_text
b1_runtime_details = _demo_service.b1_runtime_details
search_b2_vectors = _demo_service.search_b2_vectors

_ORIGINAL_B1_RUNTIME_DETAILS = _demo_service.b1_runtime_details
_ORIGINAL_SEARCH_B2_VECTORS = _demo_service.search_b2_vectors
_ORIGINAL_WAIT_FOR_B2_TASK = _demo_service.wait_for_b2_task


def demo_enabled() -> bool:
    return _demo_service.demo_enabled()


def record_demo_run(source: str, report: dict[str, Any]) -> None:
    _demo_service.record_demo_run(source, report)


def record_schedule_result(source: str, result: dict[str, Any]) -> None:
    _demo_service.record_schedule_result(source, result)


async def b1_sidecar_status() -> dict[str, Any]:
    return await _demo_service.b1_sidecar_status()


async def b1_sidecar_embedding(body: dict[str, Any]) -> dict[str, Any]:
    return await _demo_service.b1_sidecar_embedding(body)


def b3_candidates_from_context(context: Any) -> dict[str, Any]:
    return _demo_service.b3_candidates_from_context(context)


async def wait_for_b2_task(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], list[str]]:
    _demo_service.monotonic = monotonic
    return await _ORIGINAL_WAIT_FOR_B2_TASK(*args, **kwargs)


_WAIT_WRAPPER = wait_for_b2_task


async def run_full_test(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _demo_service.submit_long_text = submit_long_text
    _demo_service.b1_runtime_details = (
        _ORIGINAL_B1_RUNTIME_DETAILS
        if b1_runtime_details is _demo_service.b1_runtime_details
        else b1_runtime_details
    )
    _demo_service.search_b2_vectors = (
        _ORIGINAL_SEARCH_B2_VECTORS
        if search_b2_vectors is _demo_service.search_b2_vectors
        else search_b2_vectors
    )
    _demo_service.wait_for_b2_task = (
        _ORIGINAL_WAIT_FOR_B2_TASK
        if wait_for_b2_task is _WAIT_WRAPPER
        else wait_for_b2_task
    )
    return await _demo_service.run_full_test(*args, **kwargs)


if __name__ == "__main__":  # pragma: no cover
    main()
