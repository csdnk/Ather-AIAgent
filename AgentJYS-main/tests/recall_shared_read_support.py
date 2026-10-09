"""RC-AUTH-09 reusable controlled grant request, receipt and original Pack helpers."""

import json
import math
import os
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field, model_validator

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.contracts.models import (
    AccessObserved,
    ContextPack,
    RecallRecord,
    RecallRequest,
)
from aether_agent_memory.remember.contracts.foundation import CandidateQualificationResult
from aether_agent_memory.remember.contracts.models import MemoryRef, SourceRef
from aether_agent_memory.runtime.contracts.foundation import ConfigurationSnapshot
from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ContractModel,
    Digest,
    Identifier,
    Permission,
    Principal,
    ScopeSelector,
    TaskOperationView,
    Timestamp,
)
from recall_authorization_support import F1_TEXTS, RecallHTTP
from recall_tenant_isolation_support import F4StageObservation, load_stage_observation

MemoryKey = Literal["shared", "neighbor", "other-scope", "other-tenant", "u08-local", "u09-local"]
VARIANTS = (
    "delivery",
    "no-grant",
    "isolation",
    "read-not-write",
    "read-not-delete",
    "remember-preparation",
)


def external_path(path: Path) -> Path:
    result = path.resolve()
    if result.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("sharing controller/receipt/Pack files must be outside project")
    return result


class SharedReadyMemory(ContractModel):
    memory: MemoryRef
    text: str = Field(min_length=1)
    body_hash: Digest
    body_generation: Identifier
    projection_generation: Identifier
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    maintainer_env: Identifier

    @model_validator(mode="after")
    def exact_body(self) -> "SharedReadyMemory":
        if sha256(self.text.encode()).hexdigest() != self.body_hash:
            raise ValueError("exact fixture body/hash mismatch")
        return self


class SharedReadCase(ContractModel):
    run_id: Identifier
    memories: dict[MemoryKey, SharedReadyMemory]
    principals: dict[str, Principal]
    credential_envs: dict[str, Identifier]
    grant: AuthorizationGrant
    query: str = Field(min_length=1)
    q18_capability_id: Identifier | None = None
    control_request: Path
    control_receipts: Path
    success_directory: Path
    maintenance_export: Path
    operator_env: Identifier
    configuration: ConfigurationSnapshot
    server_settings: RecallSettings
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    backend_binding: Digest
    model_binding: Digest
    model_space: Identifier

    def request(self) -> RecallRequest:
        return RecallRequest(
            query=self.query,
            sources="working",
            selection=ScopeSelector(session_id=self.memories["shared"].memory.scope.session_id),
        )

    @model_validator(mode="after")
    def precise_grant(self) -> "SharedReadCase":
        if set(self.memories) != {
            "shared",
            "neighbor",
            "other-scope",
            "other-tenant",
            "u08-local",
            "u09-local",
        }:
            raise ValueError("six independent named fixture memories required")
        if set(self.principals) != set(self.credential_envs) or set(self.principals) != {
            "U01",
            "U08",
            "U09",
        }:
            raise ValueError("owner, independent recipient and no-grant subject required")
        shared = self.memories["shared"].memory
        if (
            self.memories["shared"].text != F1_TEXTS[0]
            or self.memories["neighbor"].text != F1_TEXTS[1]
        ):
            raise ValueError("shared and neighbor must be exact maintained F1 texts")
        home_bindings: tuple[tuple[str, MemoryKey], ...] = (
            ("U01", "shared"),
            ("U08", "u08-local"),
            ("U09", "u09-local"),
        )
        for name, key in home_bindings:
            p = self.principals[name]
            if p.principal_id != name or p.home_scope != self.memories[key].memory.scope:
                raise ValueError("actual home scopes must bind local successful controls")
            if Permission.READ not in p.permissions:
                raise ValueError("legal READ controls required")
        recipient = self.principals["U08"]
        if recipient.home_scope == self.principals["U09"].home_scope:
            raise ValueError(
                "recipient/no-grant successful controls require independent home scopes"
            )
        if not {Permission.READ, Permission.WRITE, Permission.CORRECT, Permission.DELETE}.issubset(
            recipient.permissions
        ):
            raise ValueError(
                "ambient actions required to distinguish READ grant from object ownership"
            )
        for name in ("U08", "U09"):
            home = self.principals[name].home_scope
            if home.tenant_id != shared.scope.tenant_id or any(
                getattr(home, key) == getattr(shared.scope, key)
                for key in ("application_id", "user_id", "agent_id")
            ):
                raise ValueError("same-tenant independent subjects required")
        if self.memories["neighbor"].memory.scope != shared.scope:
            raise ValueError("neighbor must be an unshared memory in the same owner scope")
        if (
            self.memories["other-scope"].memory.scope.tenant_id != shared.scope.tenant_id
            or self.memories["other-scope"].memory.scope == shared.scope
        ):
            raise ValueError("same-tenant different-scope negative required")
        if self.memories["other-scope"].memory.scope in (
            self.principals["U08"].home_scope,
            self.principals["U09"].home_scope,
        ):
            raise ValueError("negative other scope cannot be a reader's authorized home")
        if self.memories["other-tenant"].memory.scope.tenant_id == shared.scope.tenant_id:
            raise ValueError("other-tenant negative required")
        if not shared.scope.session_id or any(
            m.memory.scope.session_id != shared.scope.session_id for m in self.memories.values()
        ):
            raise ValueError("same requested session keeps selection from explaining authorization")
        if (
            len({m.memory.memory_id for m in self.memories.values()}) != 6
            or len({m.text for m in self.memories.values()}) != 6
        ):
            raise ValueError("distinct ID/body canaries required")
        g = self.grant
        if (
            g.grantee_id,
            g.grantee_tenant_id,
            g.resource.owner,
            g.resource.object_type,
            g.resource.object_id,
            g.resource.scope,
            g.permissions,
        ) != (
            "U08",
            recipient.home_scope.tenant_id,
            "remember",
            "memory",
            shared.memory_id,
            shared.scope,
            (Permission.READ,),
        ):
            raise ValueError("grant must authorize only READ of the one shared memory to U08")
        if g.resource.version not in (None, shared.version):
            raise ValueError("grant version differs from exact current target")
        if self.server_settings.candidate_limit != 1:
            raise ValueError("K=1 exact single-result fixture required")
        for path in (
            self.control_request,
            self.control_receipts,
            self.success_directory,
            self.maintenance_export,
        ):
            external_path(path)
        return self


class PermissionCheck(ContractModel):
    principal_id: Identifier
    permission: Permission
    memory: MemoryRef
    allowed: bool


class GrantReceipt(ContractModel):
    run_id: Identifier
    control_operation_id: Identifier
    q18_capability_id: Identifier
    entrypoint: Literal["Identity.provision"]
    evidence_id: Identifier
    observer_principal_id: Identifier
    source_sha: str
    config_hash: Digest
    image_digest: str
    backend_binding: Digest
    model_binding: Digest
    configuration_revision: int = Field(gt=0)
    observed_at: Timestamp
    grants_for_u08: tuple[AuthorizationGrant, ...]
    grants_for_u09: tuple[AuthorizationGrant, ...]
    qualification: CandidateQualificationResult
    no_grant_qualification: CandidateQualificationResult
    unshared_qualification: CandidateQualificationResult
    qualification_principals: dict[Literal["shared", "no-grant", "unshared"], Principal]
    permission_checks: tuple[PermissionCheck, ...]


def assert_active_grant(receipt: GrantReceipt, case: SharedReadCase, observer: str) -> None:
    assert receipt.run_id == case.run_id and receipt.control_operation_id == "share-" + case.run_id
    assert (
        receipt.q18_capability_id == case.q18_capability_id
        and receipt.observer_principal_id == observer
    )
    for field, value in (
        ("source_sha", case.source_sha),
        ("config_hash", case.configuration.config_hash),
        ("image_digest", case.image_digest),
        ("backend_binding", case.backend_binding),
        ("model_binding", case.model_binding),
    ):
        assert getattr(receipt, field) == value
    assert receipt.grants_for_u08 == (case.grant,) and not receipt.grants_for_u09
    assert receipt.qualification_principals == {
        "shared": case.principals["U08"],
        "no-grant": case.principals["U09"],
        "unshared": case.principals["U08"],
    }, "qualification must use the actual recipient/control principal"
    assert case.grant.expires_at is None or receipt.observed_at < case.grant.expires_at
    shared = case.memories["shared"]
    q = receipt.qualification
    assert q.decision == "allowed" and q.target.memory == shared.memory
    assert (
        q.target.generation == shared.projection_generation
        and q.target.body_hash == shared.body_hash
    )
    assert q.target.model_space == case.model_space and q.target.memory_source == "working"
    assert q.guard is not None and q.manifest is not None
    for negative, expected in (
        (receipt.no_grant_qualification, shared.memory),
        (receipt.unshared_qualification, case.memories["neighbor"].memory),
    ):
        assert negative.target.memory == expected and negative.decision == "excluded"
        assert negative.guard is None and negative.manifest is None
    expected_checks = {
        (Permission.WRITE, False),
        (Permission.CORRECT, False),
        (Permission.DELETE, False),
        (Permission.READ, True),
    }
    actual_checks = {
        (p.permission, p.allowed)
        for p in receipt.permission_checks
        if p.principal_id == "U08" and p.memory == shared.memory
    }
    assert actual_checks == expected_checks, "READ sharing cannot imply mutation authority"


def prepare_shared_grant(case: SharedReadCase, observer: str, timeout: float = 30) -> GrantReceipt:
    """Ask the external Q18 deployment controller, then wait for actual authority.

    The request file is an IPC fixture protocol, never a product sharing API.
    No direct SQL or Identity.provision invocation from the test process.
    """
    if not case.q18_capability_id:
        raise RuntimeError("blocked_fixture: Q18 controlled grant preparation unavailable")
    path = external_path(case.control_request)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + "." + case.run_id + ".pending")
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "run_id": case.run_id,
                "control_operation_id": "share-" + case.run_id,
                "q18_capability_id": case.q18_capability_id,
                "entrypoint": "Identity.provision",
                "grant": case.grant.model_dump(mode="json"),
            },
            stream,
        )
    os.link(pending, path)  # Atomic, and an existing request cannot be overwritten.
    pending.unlink()
    deadline = time.monotonic() + timeout
    while True:
        if case.control_receipts.is_file():
            rows = json.loads(case.control_receipts.read_text(encoding="utf-8"))
            matches = [
                row for row in rows if row.get("control_operation_id") == "share-" + case.run_id
            ]
            assert len(matches) <= 1, "duplicate grant receipt"
            if matches:
                receipt = GrantReceipt.model_validate(matches[0])
                assert_active_grant(receipt, case, observer)
                return receipt
        if time.monotonic() >= deadline:
            raise RuntimeError("blocked_fixture: authoritative active grant receipt unavailable")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


class SharedStageObservation(F4StageObservation):
    principal: Principal
    consumed_grants: tuple[AuthorizationGrant, ...]
    qualification: CandidateQualificationResult


class SavedSharedPack(ContractModel):
    operation_id: Identifier
    job_id: Identifier
    trace_id: str
    grant_evidence_id: Identifier | None
    pack: ContextPack


def assert_shared_pack(
    payload: dict[str, Any], case: SharedReadCase, principal_id: str, expected: SharedReadyMemory
) -> ContextPack:
    pack = ContextPack.model_validate(payload)
    assert pack.scope == case.principals[principal_id].home_scope
    assert pack.outcome == "available"
    items = [item for group in pack.groups for item in group.items]
    assert len(items) == 1 and items[0].memory == expected.memory
    assert items[0].content == expected.text and items[0].sources == expected.sources
    assert expected.text in pack.rendered_context
    serialized = pack.model_dump_json()
    for memory in case.memories.values():
        if memory.memory != expected.memory:
            assert memory.text not in serialized and memory.memory.memory_id not in serialized
    return pack


class SharedRecallHarness:
    """Reusable grant preparation and exact original success for revoke/model gates."""

    def __init__(self, client: httpx.Client, case: SharedReadCase, observer: str) -> None:
        self.client, self.case, self.observer = client, case, observer
        self.operator = RecallHTTP(client, os.environ[case.operator_env])
        self.events: set[str] = set()

    def prepare(self) -> GrantReceipt:
        return prepare_shared_grant(self.case, self.observer)

    def recall_and_save(
        self,
        principal_id: str,
        expected_key: MemoryKey,
        label: str,
        receipt: GrantReceipt | None = None,
    ) -> SavedSharedPack:
        case = self.case
        expected = case.memories[expected_key]
        if principal_id == "U08" and expected_key == "shared":
            assert receipt is not None
            assert_active_grant(receipt, case, self.observer)
        token = os.environ[case.credential_envs[principal_id]]
        harness = RecallHTTP(self.client, token)
        operation_id = f"shared-{case.run_id}-{label}"
        result, job_id = harness.command(
            "/p3/recall", case.request().model_dump(mode="json"), operation_id, token
        )
        pack = assert_shared_pack(result, case, principal_id, expected)
        admin = self.operator.get(f"/p3/admin/tasks/{job_id}")
        if admin.status_code != 200:
            raise RuntimeError("blocked_fixture: authorized original task view unavailable")
        assert admin.json()["task"]["task_id"] == job_id
        trace_id = admin.json()["traces"]["trace_id"]
        stage = load_stage_observation(
            case.maintenance_export, job_id, observation_type=SharedStageObservation
        )
        assert isinstance(stage, SharedStageObservation)
        assert (
            stage.run_id,
            stage.operation_id,
            stage.job_id,
            stage.recall_id,
            stage.trace_id,
        ) == (case.run_id, operation_id, job_id, pack.recall_id, trace_id)
        assert (
            stage.observer_principal_id == self.observer
            and stage.principal == case.principals[principal_id]
        )
        for field, value in (
            ("source_sha", case.source_sha),
            ("config_hash", case.configuration.config_hash),
            ("image_digest", case.image_digest),
            ("backend_binding", case.backend_binding),
            ("model_binding", case.model_binding),
        ):
            assert getattr(stage, field) == value
        query_hash = sha256(case.query.encode()).hexdigest()
        assert stage.query_hash == stage.query_embedding_input_hash == query_hash
        assert stage.qualified == stage.body_reads == stage.result_refs == (expected.memory,)
        assert len(stage.body_paths) == 1
        assert stage.model_input_refs == (
            () if case.server_settings.rerank_policy == "disabled" else (expected.memory,)
        )
        assert len(stage.candidates) == 1 and stage.candidates[0].target.memory == expected.memory
        hit = stage.candidates[0]
        assert hit.rank == 1 and hit.target.generation == expected.projection_generation
        assert (
            hit.target.body_hash == expected.body_hash
            and hit.target.model_space == case.model_space
        )
        assert hit.target.memory_source == "working"
        assert hit.score is not None and math.isfinite(hit.score) and hit.score > 0
        q = stage.qualification
        assert q.decision == "allowed" and q.target.model_dump(
            mode="json"
        ) == hit.target.model_dump(mode="json")
        assert stage.consumed_grants == ((case.grant,) if receipt is not None else ())
        assert q.guard is not None
        if receipt is not None and case.grant.expires_at is not None:
            assert q.guard.checked_at < case.grant.expires_at
        stages = set()
        for event in stage.events:
            payload = AccessObserved.model_validate(event.payload)
            assert event.event_type == "recall.access" and event.producer == "recall"
            assert payload.memory == expected.memory and payload.recall_id == pack.recall_id
            assert payload.outcome == "succeeded" and event.initiator_id == principal_id
            assert (
                event.subject.scope == expected.memory.scope
                and event.subject.object_id == expected.memory.memory_id
            )
            assert event.subject.owner == "remember" and event.subject.object_type == "memory"
            assert event.subject.version == event.subject_revision == expected.memory.version
            assert event.trace_id == trace_id and event.event_id not in self.events
            self.events.add(event.event_id)
            stages.add(payload.stage)
        assert {"read", "packed"}.issubset(stages)
        saved = SavedSharedPack(
            operation_id=operation_id,
            job_id=job_id,
            trace_id=trace_id,
            grant_evidence_id=receipt.evidence_id if receipt else None,
            pack=pack,
        )
        self.verify_original(principal_id, saved)
        directory = external_path(case.success_directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (operation_id + ".json")
        with os.fdopen(
            os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8"
        ) as stream:
            stream.write(saved.model_dump_json())
        return saved

    def verify_original(self, principal_id: str, saved: SavedSharedPack) -> None:
        harness = RecallHTTP(self.client, os.environ[self.case.credential_envs[principal_id]])
        record = harness.get(f"/p3/recalls/{saved.pack.recall_id}")
        assert record.status_code == 200
        state = RecallRecord.model_validate(record.json())
        assert (
            state.scope == saved.pack.scope
            and state.state == "completed"
            and state.result_available
        )
        job_response = harness.get(f"/p3/operations/{saved.job_id}")
        assert job_response.status_code == 200
        job = TaskOperationView.model_validate(job_response.json())
        assert job.initiator_id == principal_id and job.state == "succeeded"
        assert job.subject.scope == saved.pack.scope
        assert job.result_ref is not None and job.result_ref.scope == saved.pack.scope
        for path in (
            f"/p3/recalls/{saved.pack.recall_id}/result",
            f"/p3/operations/{saved.job_id}/result",
        ):
            response = harness.get(path)
            assert (
                response.status_code == 200
                and ContextPack.model_validate(response.json()) == saved.pack
            )
