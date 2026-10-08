"""AET-20 RC-REL-05: intrinsic relation snapshot contract validation.

Exact query coverage and cross-read revisions require the planning boundary;
an individual snapshot cannot prove those facts on its own.
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aether_agent_memory.remember.contracts.foundation import MemoryRelationSnapshot


def relation_payload():
    cases = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/p3/fixtures/cases.json").read_text(
            "utf-8"
        )
    )
    payload = next(
        c["payload"]
        for c in cases
        if c["model"] == "remember.MemoryRelationSnapshot" and c["valid"]
    )
    member = copy.deepcopy(payload["guards"][0]["memory"])
    member["memory_id"] = "related"
    payload["conflicts"] = [
        {
            "group_id": "required_group",
            "members": [payload["guards"][0]["memory"], member],
            "explanation": "Both facts are required together",
        }
    ]
    return payload


@pytest.mark.p0
def test_rc_rel_05_snapshot_may_describe_unqueried_required_members():
    snapshot = MemoryRelationSnapshot.model_validate(relation_payload())
    assert len(snapshot.guards) == 1
    assert [m.memory_id for m in snapshot.conflicts[0].members] == ["id_1", "related"]


@pytest.mark.p0
@pytest.mark.parametrize("fault", ["duplicate_guard", "duplicate_group", "wrong_ref", "no_guards"])
def test_rc_rel_05_snapshot_rejects_duplicate_or_unrelated_evidence(fault):
    payload = relation_payload()
    if fault == "duplicate_guard":
        payload["guards"].append(copy.deepcopy(payload["guards"][0]))
    elif fault == "duplicate_group":
        payload["conflicts"].append(copy.deepcopy(payload["conflicts"][0]))
    elif fault == "wrong_ref":
        payload["guards"][0]["memory"] = {
            **payload["guards"][0]["memory"],
            "memory_id": "unrelated",
        }
    else:
        payload["guards"] = []
    with pytest.raises(ValidationError):
        MemoryRelationSnapshot.model_validate(payload)
