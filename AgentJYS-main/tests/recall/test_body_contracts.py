"""AET-19 RC-BODY-04: body evidence cannot substitute content or exact Ref.

Cross-read revision comparisons and real lifecycle behavior are tested at the
planning/Remember boundaries in runtime/flows/test_recall_body.py.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aether_agent_memory.remember.contracts.foundation import FullBodyReadResult


def body_payload():
    cases = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/p3/fixtures/cases.json").read_text(
            "utf-8"
        )
    )
    return next(
        c["payload"]
        for c in cases
        if c["model"] == "remember.FullBodyReadResult"
        and c["valid"]
        and c["payload"]["outcome"] == "read"
    )


@pytest.mark.p0
@pytest.mark.parametrize("fault", ["content", "hash", "location_hash", "version", "id", "scope"])
def test_rc_body_04_full_body_contract_rejects_integrity_or_exact_ref_mismatch(fault):
    payload = body_payload()
    if fault == "content":
        payload["content"] = "untrusted replacement body"
    elif fault == "hash":
        payload["guard"]["body_hash"] = "f" * 64
    elif fault == "location_hash":
        payload["location"]["content_hash"] = "f" * 64
    elif fault == "version":
        payload["memory"]["version"] += 1
    elif fault == "id":
        payload["memory"]["memory_id"] = "other_memory"
    else:
        payload["memory"]["scope"]["tenant_id"] = "other_tenant"
    with pytest.raises(ValidationError):
        FullBodyReadResult.model_validate(payload)


@pytest.mark.p0
@pytest.mark.parametrize("outcome", ["excluded", "missing", "unavailable", "stale"])
def test_rc_body_05_failed_read_never_exposes_body_or_reusable_guard(outcome):
    payload = body_payload()
    failed = {
        "memory": payload["memory"],
        "outcome": outcome,
        "path": "none",
        "reason_code": "controlled_failure",
    }
    result = FullBodyReadResult.model_validate(failed)
    assert result.content is None and result.guard is None and not result.sources
    for field in ("content", "sources", "location", "guard"):
        with pytest.raises(ValidationError):
            FullBodyReadResult.model_validate({**failed, field: payload[field]})
