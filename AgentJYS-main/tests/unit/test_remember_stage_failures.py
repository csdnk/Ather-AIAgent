"""Remember stage diagnostics retain only technical metadata, never model data."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aether_agent_memory.remember.basic.candidate_consolidation import (
    prepare_candidate_consolidation,
)
from aether_agent_memory.remember.basic.extraction import EvidenceValidationError
from aether_agent_memory.remember.basic.official_langmem import CandidateCapacityError
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError

from .test_remember_candidate_batching import app_and_input


def failures(app):
    return [
        value for (table, _), value in app.uow.rows.items() if table == "remember_stage_failures"
    ]


@pytest.mark.parametrize("stage", ["precompression", "extraction", "decision"])
async def test_stage_failure_records_type_without_secret_or_payload(stage, monkeypatch):
    app, items, _ = app_and_input(1)
    error = RuntimeError("SECRET_API_KEY and private original quote and model response")
    if stage == "precompression":
        monkeypatch.setattr(
            "aether_agent_memory.remember.basic.candidate_consolidation._precompressed_views",
            AsyncMock(side_effect=error),
        )
    else:
        provider = (
            app.extraction.extract_candidates
            if stage == "extraction"
            else app.extraction.decide_candidates
        )
        provider.side_effect = error
    task = SimpleNamespace(task_id="diagnostic-task", kind="remember.extract")
    for _ in range(2):
        with pytest.raises(RuntimeError) as caught:
            await prepare_candidate_consolidation(app, None, task, items)
        assert caught.value is error
    recorded = failures(app)
    assert len(recorded) == 1
    assert recorded[0]["task_id"] == task.task_id
    assert recorded[0]["stage"] == stage
    assert recorded[0]["exception_type"] == "RuntimeError"
    assert recorded[0]["attempts"] == 2
    assert set(recorded[0]) == {"task_id", "stage", "binding", "exception_type", "attempts"}
    encoded = json.dumps(recorded)
    assert "SECRET_API_KEY" not in encoded and "private original" not in encoded
    assert "model response" not in encoded and items[0].content not in encoded


@pytest.mark.parametrize(
    ("error", "field", "value"),
    [
        (
            EvidenceValidationError(
                "quote_not_in_source", "private-source", "private-quote", "secret-candidate"
            ),
            "reason",
            "quote_not_in_source",
        ),
        (
            FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "secret connection details"),
            "code",
            "DEPENDENCY_UNAVAILABLE",
        ),
    ],
)
async def test_only_safe_reason_or_foundation_code_is_persisted(error, field, value):
    app, items, _ = app_and_input(1)
    app.extraction.extract_candidates.side_effect = error
    with pytest.raises(type(error)):
        await prepare_candidate_consolidation(
            app, None, SimpleNamespace(task_id="safe", kind="remember.extract"), items
        )
    row = failures(app)[0]
    assert row[field] == value
    encoded = json.dumps(row)
    assert all(
        secret not in encoded
        for secret in ("private-source", "private-quote", "secret-candidate", "secret connection")
    )


async def test_unrecognized_feedback_reason_is_not_copied_as_free_text():
    app, items, _ = app_and_input(1)
    error = EvidenceValidationError("private feedback SECRET", "source", "quote", "candidate")
    app.extraction.extract_candidates.side_effect = error
    with pytest.raises(EvidenceValidationError):
        await prepare_candidate_consolidation(
            app, None, SimpleNamespace(task_id="safe", kind="remember.extract"), items
        )
    assert "reason" not in failures(app)[0]
    assert "SECRET" not in json.dumps(failures(app))


@pytest.mark.parametrize("failure", ["logging", "invalidated"])
async def test_diagnostic_failure_does_not_replace_original_error(failure):
    app, items, _ = app_and_input(1)
    original = RuntimeError("original provider interruption")
    real_write = app.uow.write

    def write(table, key, value):
        if table == "remember_stage_failures":
            raise OSError("diagnostic database unavailable")
        real_write(table, key, value)

    async def interrupted(*args, **kwargs):
        if failure == "logging":
            app.uow.write = write
        else:

            def invalid(*args):
                raise FoundationError(ErrorCode.RESULT_INVALIDATED, "task no longer current")

            app.tasks.guard = invalid
        raise original

    app.extraction.extract_candidates.side_effect = interrupted
    with pytest.raises(RuntimeError) as caught:
        await prepare_candidate_consolidation(
            app, None, SimpleNamespace(task_id="safe", kind="remember.extract"), items
        )
    assert caught.value is original
    assert not failures(app)


async def test_success_and_handled_capacity_do_not_create_failure_diagnostics():
    # Recovery may subdivide at the complete-message boundary. A single atomic
    # sentence is intentionally no longer retried as arbitrary character halves.
    app, items, _ = app_and_input(2)
    delegate = app.extraction.extract_candidates.side_effect
    attempted = False

    async def capacity_once(*args, **kwargs):
        nonlocal attempted
        if not attempted:
            attempted = True
            raise CandidateCapacityError(0, reason="output_truncated")
        return await delegate(*args, **kwargs)

    app.extraction.extract_candidates.side_effect = capacity_once
    result = await prepare_candidate_consolidation(
        app, None, SimpleNamespace(task_id="success", kind="remember.extract"), items
    )
    assert result["proposals"]
    assert not failures(app)
    capacity = [
        row
        for (table, _), row in app.uow.rows.items()
        if table == "remember_candidate_extraction_splits"
    ]
    assert len(capacity) == 1 and capacity[0]["reason"] == "output_capacity"
