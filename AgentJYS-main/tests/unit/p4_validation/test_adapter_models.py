"""Contracts retained by the fixed-scenario P3 adapter, not the discarded browser API."""

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError as ModelError

from aether_p4_simulator.validation import models

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("value", ["", "a" * 129, "../p3", "x?y", "x\r\ny", "中文", 1, True, None])
def test_operation_ids_reject_unsafe_or_non_string_values(value):
    with pytest.raises(ModelError):
        TypeAdapter(models.OperationID).validate_python(value)


@pytest.mark.parametrize("value", ["a", "A_0-z", "a" * 128])
def test_operation_id_boundaries_are_preserved(value):
    assert TypeAdapter(models.OperationID).validate_python(value) == value


@pytest.mark.parametrize("status_code", [99, 600, True, 200.0, "200"])
def test_probe_status_codes_remain_strict_and_bounded(status_code):
    with pytest.raises(ModelError):
        models.ProbeResult[models.LiveData](
            ok=False, status_code=status_code, data=None, error=None
        )


def test_response_models_expose_only_named_contract_fields():
    result = models.ConsolidateData.model_validate(
        {"task_ids": ["task-1"], "credential": "secret-fixture", "debug": {"url": "private"}}
    )
    assert result.model_dump(mode="json") == {"task_ids": ["task-1"]}
    with pytest.raises(ModelError, match="frozen"):
        result.task_ids = ()


def test_discarded_per_action_browser_contracts_are_not_exposed():
    discarded = {
        "SessionInput",
        "RememberInput",
        "RecallInput",
        "CorrectInput",
        "ConsolidateInput",
        "SessionInfo",
        "Envelope",
        "ActionResult",
    }
    assert discarded.isdisjoint(vars(models)), "Remove the unused V1 browser API placeholders"
