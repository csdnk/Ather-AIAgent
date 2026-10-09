"""RC-AUTH-14 ownership matrix and Q01/Q06-bound public response checks."""

import json
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field, model_validator

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.contracts.models import ContextPack, RecallRecord, RecallRequest
from aether_agent_memory.runtime.contracts.foundation import ConfigurationSnapshot, NodeLogRecord
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Digest,
    Identifier,
    Permission,
    Principal,
    TaskOperationView,
    TaskRecord,
)
from recall_authorization_support import F1_TEXTS, assert_denied
from recall_forged_authorization_support import assert_no_index_disclosure
from recall_shared_read_support import SavedSharedPack, SharedReadyMemory, external_path
from recall_tenant_isolation_support import F4StageObservation

VIEWERS = ("U01", "U06", "U07", "U02-user", "U02-agent")
SURFACES = (
    "diagnostic-task",
    "trace-list",
    "trace-detail",
    "job",
    "record",
    "recall-result",
    "operation-result",
)
VARIANTS = tuple(f"{viewer}:{surface}" for viewer in VIEWERS for surface in SURFACES)
RESULT_SURFACES = {"recall-result", "operation-result"}


class MatrixRule(ContractModel):
    decision: Literal["visible", "filtered", "denied"]
    status: Literal[200, 403, 404]
    code: Literal["FORBIDDEN", "NOT_FOUND"] | None = None
    allowed_fields: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def http_mapping(self) -> "MatrixRule":
        if self.decision == "denied":
            if (self.status, self.code) not in {(403, "FORBIDDEN"), (404, "NOT_FOUND")}:
                raise ValueError("denial must bind current Q01 HTTP mapping")
        elif self.status != 200 or self.code is not None:
            raise ValueError("visible/filtered response requires HTTP 200")
        if len(set(self.allowed_fields)) != len(self.allowed_fields):
            raise ValueError("duplicate allowed response field")
        return self


class MatrixPolicy(ContractModel):
    q01_evidence_id: Identifier | None = None
    q06_evidence_id: Identifier | None = None
    policy_sha256: Digest
    source_sha: str
    rules: dict[str, MatrixRule]


class OwnershipCase(ContractModel):
    run_id: Identifier
    principals: dict[str, Principal]
    credential_envs: dict[str, Identifier]
    foreign_dimension: Literal["user_id", "agent_id"]
    memories: tuple[SharedReadyMemory, SharedReadyMemory]
    request: RecallRequest
    operator_env: Identifier
    configuration: ConfigurationSnapshot
    server_settings: RecallSettings
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    backend_binding: Digest
    model_binding: Digest
    model_space: Identifier
    maintenance_export: Path
    success_directory: Path
    policy_file: Path

    @model_validator(mode="after")
    def actors_and_f1(self) -> "OwnershipCase":
        if set(self.principals) != {"U01", "U06", "U07", "U02"} or set(self.credential_envs) != set(
            self.principals
        ):
            raise ValueError("four actual identities with separate ordinary credentials required")
        expected = {
            "U01": {Permission.READ, Permission.DIAGNOSE},
            "U06": {Permission.READ, Permission.DIAGNOSE},
            "U07": {Permission.DIAGNOSE},
            "U02": {Permission.READ, Permission.DIAGNOSE},
        }
        owner = self.principals["U01"]
        for name, principal in self.principals.items():
            if principal.principal_id != name or set(principal.permissions) != expected[name]:
                raise ValueError("matrix requires exact READ/DIAGNOSE permission combinations")
            differences = {
                key
                for key, value in owner.home_scope.model_dump().items()
                if getattr(principal.home_scope, key) != value
            }
            if differences != ({self.foreign_dimension} if name == "U02" else set()):
                raise ValueError("U06/U07 same scope; U02 differs only in declared user/agent")
        if tuple(m.text for m in self.memories) != F1_TEXTS:
            raise ValueError("exact F1 Ready coffee/tea pair required")
        if any(m.memory.scope != owner.home_scope for m in self.memories):
            raise ValueError("F1 must bind original initiator's exact scope")
        if len({m.memory.memory_id for m in self.memories}) != 2:
            raise ValueError("independent F1 references required")
        if (
            self.request.sources != "working"
            or not owner.home_scope.session_id
            or self.request.selection.model_dump(exclude_none=True)
            != {"session_id": owner.home_scope.session_id}
            or self.server_settings.candidate_limit != 1
        ):
            raise ValueError("bound Working K=1 original success required")
        paths = [
            external_path(p)
            for p in (self.maintenance_export, self.success_directory, self.policy_file)
        ]
        if any(
            a == b or a in b.parents or b in a.parents
            for index, a in enumerate(paths)
            for b in paths[index + 1 :]
        ):
            raise ValueError("independent external evidence/policy paths required")
        return self


class OwnershipStage(F4StageObservation):
    principal: Principal
    consumed_grant_ids: tuple[Identifier, ...]
    maintenance_principal_ids: tuple[Identifier, ...]


def endpoint(surface: str, saved: SavedSharedPack) -> str:
    return {
        "diagnostic-task": f"/p3/tasks/{saved.job_id}",
        "trace-list": "/p3/traces",
        "trace-detail": f"/p3/logs/{saved.trace_id}",
        "job": f"/p3/operations/{saved.job_id}",
        "record": f"/p3/recalls/{saved.pack.recall_id}",
        "recall-result": f"/p3/recalls/{saved.pack.recall_id}/result",
        "operation-result": f"/p3/operations/{saved.job_id}/result",
    }[surface]


def read_policy(case: OwnershipCase, variant: str) -> tuple[MatrixPolicy | None, MatrixRule | None]:
    path = external_path(case.policy_file)
    if not path.is_file():
        return None, None
    raw = json.loads(path.read_text())
    policy = MatrixPolicy.model_validate(raw)
    from hashlib import sha256

    # Policy digest covers exact Q01/Q06/rules/source payload; cannot merely echo a fixture digest.
    payload = {key: value for key, value in raw.items() if key != "policy_sha256"}
    actual = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert policy.policy_sha256 == actual and policy.source_sha == case.source_sha
    if not policy.q01_evidence_id or not policy.q06_evidence_id:
        return policy, None
    return policy, policy.rules.get(variant)


def assert_no_secrets(payload: Any, secrets: tuple[str, ...]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False)
    assert not any(secret and secret in encoded for secret in secrets), "credential leak"


def assert_metadata(payload: Any) -> None:
    """Body prohibition is independent of whether Q01 has resolved the cell's status."""
    assert_no_index_disclosure(payload, tuple(text.rstrip("。") for text in F1_TEXTS))
    if isinstance(payload, dict):
        assert not {
            "content",
            "body",
            "rendered_context",
            "pack",
            "groups",
            "candidates",
            "credential",
            "credential_sha256",
            "token",
            "authorization",
            "raw_payload",
            "input",
            "output",
            "exception",
            "stacktrace",
        }.intersection(payload), "diagnostic body/secret field"
        for child in payload.values():
            assert_metadata(child)
    elif isinstance(payload, list):
        for child in payload:
            assert_metadata(child)


def assert_safety(
    response: httpx.Response,
    case: OwnershipCase,
    saved: SavedSharedPack,
    viewer: str,
    surface: str,
    secrets: tuple[str, ...],
) -> None:
    payload = response.json()
    assert response.status_code != 304, "HTTP cache must not bypass ownership"
    assert_no_secrets(payload, secrets)
    assert_no_secrets(dict(response.headers), secrets)
    assert_metadata(dict(response.headers))
    principal = case.principals[viewer.split("-")[0]]
    body_authorized = Permission.READ in principal.permissions and viewer in {"U01", "U06"}
    if surface not in RESULT_SURFACES or not body_authorized or response.status_code != 200:
        assert_metadata(payload)
    if response.status_code >= 400:
        # Public errors must not echo protected objects, even when the caller supplied an ID.
        private = (
            saved.trace_id,
            saved.job_id,
            saved.pack.recall_id,
            saved.operation_id,
            *(m.memory.memory_id for m in case.memories),
        )
        assert_no_index_disclosure(payload, private)
        assert_no_index_disclosure(dict(response.headers), private)
    if viewer.startswith("U02"):
        private = (
            saved.job_id,
            saved.pack.recall_id,
            saved.operation_id,
            *(m.memory.memory_id for m in case.memories),
        )
        assert_no_index_disclosure(payload, private)
    if viewer != "U01" and surface == "trace-list" and response.status_code == 200:
        assert saved.trace_id not in json.dumps(payload), "noninitiator saw original Trace"
    if viewer != "U01" and surface == "trace-detail" and response.status_code == 200:
        assert set(payload) <= LOG_PAGE_FIELDS
        assert payload.get("records") == [] and payload.get("next_after") is None
        overview = payload.get("overview", {})
        assert (
            overview.get("span_count")
            == overview.get("failed_span_count")
            == overview.get("open_span_count")
            == 0
        )
        assert overview.get("open_span_ids") == [], "foreign Trace facts leaked"
        assert (
            payload.get("trace_id") == saved.trace_id
        )  # Existing page echoes the supplied trace ID.
        assert_no_index_disclosure(dict(response.headers), (saved.trace_id,))


TRACE_ROW_FIELDS = {
    "trace_id",
    "first_sequence",
    "started_at",
    "last_seen",
    "record_count",
    "span_count",
    "failed_span_count",
    "entry_node",
    "flow",
}
LOG_PAGE_FIELDS = {
    "trace_id",
    "records",
    "next_after",
    "coverage",
    "last_pruned_at",
    "local_dropped_records",
    "overview",
}
OVERVIEW_FIELDS = {
    "span_count",
    "failed_span_count",
    "open_span_count",
    "open_span_ids",
    "open_meaning",
}


def assert_visible(
    payload: dict[str, Any],
    surface: str,
    saved: SavedSharedPack,
    case: OwnershipCase,
    viewer: str,
    *,
    require_trace_records: bool = True,
) -> None:
    if surface == "diagnostic-task":
        task = TaskRecord.model_validate(payload)
        assert task.task_id == saved.job_id and task.idempotency_key == saved.operation_id
        assert task.initiator_id == "U01" and task.subject.scope == saved.pack.scope
        assert task.state == "succeeded"
    elif surface == "job":
        job = TaskOperationView.model_validate(payload)
        assert job.task_id == saved.job_id and job.idempotency_key == saved.operation_id
        assert job.initiator_id == "U01" and job.subject.scope == saved.pack.scope
        assert job.state == "succeeded" and job.result_ref is not None
    elif surface == "record":
        record = RecallRecord.model_validate(payload)
        assert record.recall_id == saved.pack.recall_id and record.scope == saved.pack.scope
        assert record.state == "completed" and record.result_available
    elif surface in RESULT_SURFACES:
        assert viewer in {"U01", "U06"}, "DIAGNOSE/outside scope cannot authorize a Pack"
        assert ContextPack.model_validate(payload) == saved.pack
    elif surface == "trace-list":
        assert set(payload) == {"items", "next_before", "coverage"}
        assert payload["coverage"] == "retained_records_only"
        assert all(set(row) == TRACE_ROW_FIELDS for row in payload["items"])
    else:
        assert surface == "trace-detail" and set(payload) == LOG_PAGE_FIELDS
        assert (
            payload["trace_id"] == saved.trace_id and payload["coverage"] == "retained_records_only"
        )
        assert set(payload["overview"]) == OVERVIEW_FIELDS
        if viewer == "U01" and require_trace_records:
            assert payload["records"], "retained original trace evidence missing"
        for row in payload["records"]:
            sequence = row["sequence"]
            assert isinstance(sequence, int) and sequence > 0
            log = NodeLogRecord.model_validate({k: v for k, v in row.items() if k != "sequence"})
            assert log.trace_id == saved.trace_id and log.operation_id == saved.operation_id


def assert_rule(
    response: httpx.Response,
    rule: MatrixRule,
    surface: str,
    saved: SavedSharedPack,
    case: OwnershipCase,
    viewer: str,
) -> None:
    assert response.status_code == rule.status, "Q01 HTTP mapping differs"
    payload = response.json()
    assert set(payload) <= set(rule.allowed_fields), "Q06 response field allowlist exceeded"
    if rule.decision == "denied":
        assert_denied(response, rule.status, rule.code or "")
        return
    if surface == "trace-detail" and viewer != "U01":
        assert rule.decision == "filtered", "noninitiator cannot be granted an entire Trace"
    if surface == "trace-list" and viewer != "U01":
        assert rule.decision == "filtered"
    assert_visible(payload, surface, saved, case, viewer)
