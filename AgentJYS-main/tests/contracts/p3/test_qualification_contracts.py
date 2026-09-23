"""Consumer requirements for B qualification and final transaction evidence."""
# B 接口消费者契约：验证允许结果的精确证据绑定，以及最终复核清单的成员约束。
# 这些测试可由 B 实现复用，但数据模型校验通过不等于真实 B 业务已经实现。

import copy
import json

import pytest
from catalog import ROOT
from pydantic import ValidationError

from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationResult,
    CandidateQualificationTarget,
    ContextGuardRequest,
)


def evidence():
    # 从正式契约样例提取候选证据，使正反例与交付的 Schema 保持同一份基准。
    cases = json.loads((ROOT / "contracts/p3/fixtures/cases.json").read_text("utf-8"))
    return next(
        c["payload"] for c in cases if c["model"] == "recall.MemoryCandidate" and c["valid"]
    )


def qualification():
    candidate = evidence()
    hit = candidate["hits"][0]
    target = {k: v for k, v in hit.items() if k in CandidateQualificationTarget.model_fields}
    return dict(target=target, decision="allowed", reason_code="eligible",
                manifest=candidate["manifest"], guard=candidate["guard"])


@pytest.mark.parametrize("field,value", [
    ("generation", "different"), ("body_hash", "f" * 64), ("model_space", "different"),
    ("chunk_index", 999), ("vector_id", "f" * 64), ("input_hash", "f" * 64),
])
def test_qualification_rejects_mismatched_hit(field, value):
    # 每次只改变一个块身份字段，确认凭据不能被挪用到另一个块、模型空间或发布批次。
    payload = qualification()
    CandidateQualificationResult.model_validate(payload)
    payload["target"][field] = value
    with pytest.raises(ValidationError):
        CandidateQualificationResult.model_validate(payload)


@pytest.mark.parametrize("decision", ["excluded", "unverifiable"])
def test_non_allowed_must_not_expose_evidence(decision):
    # 明确排除与无法核验都不能携带可被误用为授权证明的清单和守卫。
    payload = qualification()
    payload["decision"] = decision
    with pytest.raises(ValidationError):
        CandidateQualificationResult.model_validate(payload)
    payload.update(manifest=None, guard=None)
    CandidateQualificationResult.model_validate(payload)


def test_context_guards_bind_all_manifests_and_reject_duplicates():
    candidate = evidence()
    payload = dict(expected=[candidate["guard"]], manifests=[candidate["manifest"]])
    ContextGuardRequest.model_validate(payload)
    duplicate = copy.deepcopy(payload)
    duplicate["expected"] *= 2
    with pytest.raises(ValidationError):
        ContextGuardRequest.model_validate(duplicate)
    payload["expected"] = []
    with pytest.raises(ValidationError):
        ContextGuardRequest.model_validate(payload)


@pytest.mark.parametrize("fault", ["manifest", "guard", "body_hash", "scope", "state"])
def test_allowed_requires_current_exact_evidence(fault):
    payload = qualification()
    if fault in {"manifest", "guard"}:
        payload[fault] = None
    elif fault == "state":
        payload["manifest"]["state"] = "building"
    elif fault == "scope":
        payload["guard"]["memory"]["scope"]["tenant_id"] = "other"
    else:
        payload["guard"]["body_hash"] = "f" * 64
    with pytest.raises(ValidationError):
        CandidateQualificationResult.model_validate(payload)


@pytest.mark.parametrize("fault", ["tenant", "version", "hash"])
def test_context_rejects_conflicting_members(fault):
    candidate = evidence()
    payload = dict(expected=[candidate["guard"]], manifests=[candidate["manifest"]])
    if fault == "hash":
        payload["expected"][0]["body_hash"] = "f" * 64
    else:
        other = copy.deepcopy(candidate["guard"])
        if fault == "tenant":
            other["memory"]["scope"]["tenant_id"] = "other"
        else:
            other["memory"]["version"] += 1
        payload["expected"].append(other)
    with pytest.raises(ValidationError):
        ContextGuardRequest.model_validate(payload)
