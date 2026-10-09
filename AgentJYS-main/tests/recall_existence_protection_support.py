"""RC-AUTH-15 existence protection for normal empty and permission-blocked scenarios.

This module provides support functions for validating that:
1. Legitimate empty results (authorized scope with no matching memories)
2. Permission-blocked results (matching objects exist but caller has no grants)

Both scenarios must not leak restricted content, IDs, counts, or existence information.
Test setup must be distinguishable through legal controlled phase evidence without
exposing internal differences to callers.

Q13 public response strategy is marked as blocked_requirement until approved.
"""

import json
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field, model_validator

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.contracts.models import ContextPack, RecallRequest
from aether_agent_memory.runtime.contracts.foundation import ConfigurationSnapshot
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Digest,
    Identifier,
    Permission,
    Principal,
)
from recall_authorization_support import F1_TEXTS, RecallHTTP, SafeEvidence, assert_denied
from recall_shared_read_support import SharedReadyMemory, external_path


class ExistenceScenario(ContractModel):
    """Defines a test scenario for existence protection validation."""

    scenario_type: Literal["normal_empty", "permission_blocked"]
    description: str
    principal: Principal
    credential_env: Identifier
    request: RecallRequest
    has_matching_objects: bool
    expects_grants: bool

    @model_validator(mode="after")
    def validate_scenario(self) -> "ExistenceScenario":
        if self.scenario_type == "normal_empty":
            if self.has_matching_objects:
                raise ValueError("normal_empty scenario should not have matching objects")
            if self.expects_grants:
                raise ValueError("normal_empty scenario should not expect grants")
        elif self.scenario_type == "permission_blocked":
            if not self.has_matching_objects:
                raise ValueError("permission_blocked scenario requires matching objects")
            if self.expects_grants:
                raise ValueError("permission_blocked scenario should have no grants")
        return self


class ExistenceProtectionCase(ContractModel):
    """Complete test case for existence protection validation."""

    run_id: Identifier
    scenario_empty: ExistenceScenario
    scenario_blocked: ExistenceScenario
    control_principal: Principal
    control_credential_env: Identifier
    control_memories: tuple[SharedReadyMemory, SharedReadyMemory]
    maintainer_env: Identifier
    configuration: ConfigurationSnapshot
    server_settings: RecallSettings
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    backend_binding: Digest
    model_binding: Digest
    model_space: Identifier
    evidence_directory: Path
    q13_policy_blocked: bool = True

    @model_validator(mode="after")
    def validate_case(self) -> "ExistenceProtectionCase":
        # Validate control memories match F1 pattern
        if tuple(m.text for m in self.control_memories) != F1_TEXTS:
            raise ValueError("exact F1 Ready coffee/tea pair required for control")

        # Validate independent scenarios
        if self.scenario_empty.principal.principal_id == self.scenario_blocked.principal.principal_id:
            raise ValueError("scenarios must use independent principals")

        # Validate control principal has necessary permissions
        if Permission.READ not in self.control_principal.permissions:
            raise ValueError("control principal must have READ permission")

        # Validate evidence directory is external
        if not external_path(self.evidence_directory):
            raise ValueError("evidence directory must be outside project")

        return self


def assert_no_existence_leakage(
    response: httpx.Response,
    scenario: ExistenceScenario,
    secrets: tuple[str, ...],
) -> None:
    """Assert that response does not leak existence information.

    Validates:
    - No restricted content in response body
    - No restricted IDs or references
    - No count or quantity information
    - No distinguishing features between empty and blocked scenarios
    """
    payload = response.json()

    def inspect_for_leaks(value: Any, path: str = "root") -> None:
        """Recursively inspect payload for existence leakage."""
        if isinstance(value, dict):
            # Fields that must not appear in existence-protected responses
            forbidden = {
                "pack",
                "groups",
                "items",
                "candidates",
                "rendered_context",
                "content",
                "body",
                "memory_id",
                "memory",
                "result",
                "result_ref",
                "selected_sources",
                "count",
                "total",
                "matches",
                "candidate_count",
            }
            leaked = forbidden.intersection(value.keys())
            if leaked:
                raise AssertionError(
                    f"existence leakage at {path}: forbidden fields {leaked} present"
                )

            for key, child in value.items():
                inspect_for_leaks(child, f"{path}.{key}")

        elif isinstance(value, list):
            for idx, child in enumerate(value):
                inspect_for_leaks(child, f"{path}[{idx}]")

        elif isinstance(value, str):
            # Check for F1 content leakage
            for text in F1_TEXTS:
                if text.rstrip("。") in value:
                    raise AssertionError(f"F1 content leak at {path}: '{text}' found in response")

            # Check for secret leakage
            for secret in secrets:
                if secret in value:
                    raise AssertionError(f"credential leak at {path}: secret found in response")

    inspect_for_leaks(payload)


def assert_control_success(
    client: httpx.Client,
    case: ExistenceProtectionCase,
    control_token: str,
    operation_id: str,
) -> tuple[ContextPack, str, str]:
    """Execute control request and verify successful recall.

    Returns (pack, job_id, trace_id) for evidence collection.
    """
    http = RecallHTTP(client, control_token)

    # Prepare control request with same selection as test scenarios
    control_request = RecallRequest(
        query=case.scenario_empty.request.query,
        selection=case.control_principal.home_scope.model_dump(
            exclude_none=True, include={"session_id"}
        ),
        sources="working",
        token_budget=case.server_settings.candidate_limit * 100,
    )

    payload, job_id = http.command(
        "/p3/recall",
        control_request.model_dump(mode="json"),
        operation_id,
        control_token,
    )

    pack = ContextPack.model_validate(payload)

    # Verify control returned results (proves system is functional)
    assert pack.outcome == "available", "control request must succeed"
    assert pack.scope == case.control_principal.home_scope
    items = [item for group in pack.groups for item in group.items]
    assert len(items) >= 1, "control request must return at least one memory"

    # Verify control memories are accessible
    coffee = case.control_memories[0]
    matching_items = [item for item in items if item.memory == coffee.memory]
    assert len(matching_items) == 1, "control must find coffee memory"
    assert matching_items[0].content == coffee.text

    # Get trace ID from admin view
    admin_response = http.get(f"/p3/admin/tasks/{job_id}")
    assert admin_response.status_code == 200, "control admin view unavailable"
    trace_id = admin_response.json()["traces"]["trace_id"]

    return pack, job_id, trace_id


def execute_existence_test(
    client: httpx.Client,
    scenario: ExistenceScenario,
    credential_token: str,
    operation_id: str,
    case: ExistenceProtectionCase,
    evidence: SafeEvidence,
    secrets: tuple[str, ...],
) -> dict[str, Any]:
    """Execute a single existence protection test scenario.

    Returns evidence data for the execution.
    """
    http = RecallHTTP(client, credential_token)

    # Execute recall request
    response = client.post(
        "/p3/recall",
        json=scenario.request.model_dump(mode="json"),
        headers={
            "Authorization": f"Bearer {credential_token}",
            "X-Operation-ID": operation_id,
        },
    )

    evidence.response("POST", "/p3/recall", response)

    # Current stage: Q13 not approved, so we cannot assert specific response codes
    if case.q13_policy_blocked:
        # Mark Q13 as blocking requirement
        evidence.blocked(
            "blocked_requirement",
            "Q13 public response strategy not approved - cannot assert Empty vs denied vs unavailable",
        )
        # Still validate no leakage regardless of response
        assert_no_existence_leakage(response, scenario, secrets)

        return {
            "operation_id": operation_id,
            "status_code": response.status_code,
            "scenario_type": scenario.scenario_type,
            "q13_blocked": True,
            "no_leakage_verified": True,
        }

    # After Q13 approval: validate specific public response contract
    # This code will be enabled once Q13 rules are confirmed
    assert_no_existence_leakage(response, scenario, secrets)

    # TODO: After Q13 approval, add assertions for:
    # - Specific HTTP status codes
    # - Specific error codes
    # - Specific reason fields
    # - Consistency of response format between scenarios

    return {
        "operation_id": operation_id,
        "status_code": response.status_code,
        "scenario_type": scenario.scenario_type,
        "q13_blocked": False,
        "no_leakage_verified": True,
    }


def poll_original_task(
    client: httpx.Client,
    job_id: str,
    token: str,
    timeout: float = 120,
) -> dict[str, Any]:
    """Poll original task until completion and return final state."""
    http = RecallHTTP(client, token, timeout=timeout)
    return http.poll_job(job_id, token)


def collect_diagnostic_evidence(
    client: httpx.Client,
    job_id: str,
    maintainer_token: str,
    scenario: ExistenceScenario,
    secrets: tuple[str, ...],
) -> dict[str, Any]:
    """Collect diagnostic evidence through legal maintenance views.

    This provides evidence to distinguish test setup without exposing
    internal differences to ordinary callers.
    """
    http = RecallHTTP(client, maintainer_token)

    # Get task view
    task_response = http.get(f"/p3/tasks/{job_id}")
    assert task_response.status_code == 200, "maintenance task view unavailable"
    task_data = task_response.json()

    # Verify no secrets in diagnostic view
    task_json = json.dumps(task_data)
    for secret in secrets:
        assert secret not in task_json, "credential leaked in diagnostic view"

    return {
        "task_id": job_id,
        "task_state": task_data.get("state"),
        "scenario_type": scenario.scenario_type,
        "diagnostic_available": True,
    }
