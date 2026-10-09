"""AET-25 / RC-AUTH-06 live HTTP cases; no product/environment repairs."""

import json
import os
import time
from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.recall.contracts.models import RecallRecord, RecallRequest
from aether_agent_memory.remember.contracts.foundation import FullBodyReadResult
from aether_agent_memory.remember.contracts.models import MemorySnapshot
from aether_agent_memory.runtime.contracts.models import (
    ErrorResponse,
    Permission,
    TaskOperationView,
)
from aether_agent_memory.runtime.contracts.operation_lookup import OperationLookup
from recall_authorization_support import RecallHTTP, SafeEvidence
from recall_selection_intersection_support import (
    VARIANT_STEPS,
    SelectionIntersectionCase,
    SelectionStageObservation,
    assert_selection_pack,
    assert_selection_pressure,
    assert_selection_stage,
)
from recall_tenant_isolation_support import assert_public_isolation, load_stage_observation

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
def selection_target(request):
    variant = request.node.callspec.params["variant"]
    evidence = SafeEvidence("RC-AUTH-06", variant)
    evidence.data.update(acceptance="not_evaluated", http_checks="not_run")
    directory = (
        Path(
            os.environ.get(
                "P3_RECALL_EVIDENCE_DIR",
                str(Path(gettempdir()) / "aether-workspace-support/AET-25"),
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(reason):
        evidence.blocked("blocked_fixture", reason)
        pytest.fail("blocked_fixture: " + reason)

    path = os.environ.get("P3_AUTH06_FIXTURE_FILE")
    if not path or not Path(path).is_file():
        blocked("maintainer-prepared P3_AUTH06_FIXTURE_FILE unavailable")
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if variant not in manifest["cases"]:
        blocked("independent selection variant " + variant + " unavailable")
    if not manifest["cases"][variant].get("authorization_evidence_id"):
        evidence.blocked("blocked_requirement", "authoritative ownership/qualification policy")
        pytest.fail("blocked_requirement: authoritative ownership/qualification policy unavailable")
    cases = {
        name: SelectionIntersectionCase.model_validate(value)
        for name, value in manifest["cases"].items()
    }
    case = cases[variant]
    tenants = [value.principal_scope.tenant_id for value in cases.values()]
    assert len(set(tenants)) == len(cases), "each variant must own independent data/scope"
    assert len({value.deployment.run_id for value in cases.values()}) == len(cases)
    url = urlsplit(manifest["base_url"])
    assert (
        url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
    )
    assert url.path in {"", "/"} and not url.query and not url.fragment
    all_memories = (*case.memories, case.deployment.actors[1])
    required = {case.deployment.operator_env} | {
        name for actor in all_memories for name in (actor.credential_env, actor.maintainer_env)
    }
    if any(not os.environ.get(name) for name in required):
        blocked("reader/scoped maintainer/platform operator credentials unavailable")
    token = os.environ[case.memories[0].credential_env]
    maintenance = {os.environ[case.deployment.operator_env]} | {
        os.environ[a.maintainer_env] for a in all_memories
    }
    assert (
        token not in maintenance and token != os.environ[case.deployment.actors[1].credential_env]
    )
    # Mutable response guard is switched to the selection of each original request,
    # including when earlier durable results are revisited after a selection change.
    forbidden = []

    def inspect(response):
        credential = response.request.headers.get("Authorization", "").removeprefix("Bearer ")
        if credential == token:
            response.read()
            assert_public_isolation(response.json(), tuple(forbidden))
            assert_public_isolation(dict(response.headers), tuple(forbidden))
            if response.status_code >= 400 and "code" in response.json():
                error = ErrorResponse.model_validate(response.json())
                assert error.message == error.code.value.lower()
            evidence.response(response.request.method, response.request.url.path, response)

    deployment = case.deployment
    evidence.data.update(
        lane="live-http-maintained-F4",
        run_id=deployment.run_id,
        source_sha=deployment.source_sha,
        image_digest=deployment.image_digest,
        config_hash=deployment.configuration.config_hash,
        server_candidate_limit=1,
        backend_binding=deployment.backend_binding,
        model_binding=deployment.model_binding,
        authorization_evidence_id=case.authorization_evidence_id,
        empty_selection_contract_id=case.empty_contract.evidence_id
        if case.empty_contract
        else None,
        memories=[
            {
                "ref": a.memory.model_dump(mode="json"),
                "body_hash": a.body_hash,
                "body_generation": a.body_generation,
                "projection_generation": a.projection_generation,
            }
            for a in all_memories
        ],
    )
    with httpx.Client(
        base_url=manifest["base_url"],
        timeout=30,
        follow_redirects=False,
        event_hooks={"response": [inspect]},
    ) as client:
        deadline = time.monotonic() + 60
        while client.get("/p3/readyz").status_code != 200:
            if time.monotonic() >= deadline:
                blocked("target Ready barrier unavailable")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield client, case, evidence, forbidden


@pytest.mark.parametrize("variant", tuple(VARIANT_STEPS))
def test_rc_auth_06_session_task_intersect_authorization_and_do_not_stick(
    variant, selection_target
):
    client, case, evidence, forbidden = selection_target
    deployment = case.deployment
    actor = case.memories[0]
    token = os.environ[actor.credential_env]
    harness = RecallHTTP(client, os.environ[actor.maintainer_env])
    operator = RecallHTTP(client, os.environ[deployment.operator_env])
    foreign = deployment.actors[1]
    operator_me = operator.get("/p3/auth/me")
    assert operator_me.status_code == 200
    configuration = operator.get("/p3/configuration")
    assert (
        configuration.status_code == 200
        and configuration.json() == deployment.configuration.model_dump(mode="json")
    )
    identity = harness.get("/p3/auth/me", token)
    assert identity.status_code == 200 and identity.json()["principal_id"] == "U01"
    assert identity.json()["scope"] == case.principal_scope.model_dump(mode="json")
    assert Permission.READ.value in identity.json()["permissions"]
    original_results, event_ids = [], set()

    def guard(selection):
        eligible = case.eligible(selection)
        if not selection.model_dump(exclude_none=True) and case.empty_contract:
            eligible = case.empty_contract.eligible_refs
        excluded = [a for a in (*case.memories, foreign) if a.memory not in eligible]
        included_source_ids = {
            s.source_id for a in case.memories if a.memory in eligible for s in a.sources
        }
        forbidden[:] = [atom for a in excluded for atom in (a.memory.memory_id, a.text)]
        forbidden.extend(
            s.source_id
            for a in excluded
            for s in a.sources
            if s.source_id not in included_source_ids
        )

    def execute(request_body, expected, label, pressure=False):
        guard(request_body.selection)
        operation_id = f"selection-{deployment.run_id}-{label}"
        payload = request_body.model_dump(mode="json")
        assert not {"k", "top_k", "candidate_limit"}.intersection(payload)
        result, job_id = harness.command("/p3/recall", payload, operation_id, token)
        pack = assert_selection_pack(result, case, request_body.selection, expected)
        effective_scope = case.result_scope(request_body.selection)
        record_response = harness.get(f"/p3/recalls/{pack.recall_id}", token)
        assert record_response.status_code == 200
        record = RecallRecord.model_validate(record_response.json())
        assert (
            record.scope == effective_scope
            and record.state == "completed"
            and record.result_available
        )
        job_response = harness.get(f"/p3/operations/{job_id}", token)
        assert job_response.status_code == 200
        job = TaskOperationView.model_validate(job_response.json())
        assert (
            job.state == "succeeded"
            and job.initiator_id == "U01"
            and job.subject.scope == effective_scope
        )
        assert job.result_ref is not None and job.result_ref.scope == effective_scope
        for path in (f"/p3/recalls/{pack.recall_id}/result", f"/p3/operations/{job_id}/result"):
            response = harness.get(path, token)
            assert response.status_code == 200 and response.json() == result
        admin = operator.get(f"/p3/admin/tasks/{job_id}")
        if admin.status_code != 200:
            evidence.blocked("blocked_fixture", "legal maintenance task view unavailable")
            pytest.fail("blocked_fixture: legal maintenance task view unavailable")
        assert admin.json()["task"]["task_id"] == job_id
        trace_id = admin.json()["traces"]["trace_id"]
        try:
            stage = load_stage_observation(
                deployment.maintenance_export, job_id, observation_type=SelectionStageObservation
            )
        except RuntimeError:
            evidence.blocked(
                "blocked_fixture", "authorized original-job selection/qualification receipt"
            )
            raise
        assert isinstance(stage, SelectionStageObservation)
        assert_selection_stage(
            stage,
            case,
            request_body,
            expected,
            operation_id,
            job_id,
            pack.recall_id,
            trace_id,
            operator_me.json()["principal_id"],
        )
        current_event_ids = {event.event_id for event in stage.events}
        assert not event_ids.intersection(current_event_ids), "cross-operation event collision"
        event_ids.update(current_event_ids)
        if pressure:
            assert expected is not None
            assert_selection_pressure(
                [h.model_dump(mode="json") for h in stage.unfiltered_probe],
                case,
                request_body.selection,
                expected,
            )
        assert stage.result_refs == tuple(
            item.memory for group in pack.groups for item in group.items
        )
        original_results.append((request_body.selection, pack.recall_id, job_id, result))
        evidence.data.setdefault("operations", []).append(
            {
                "label": label,
                "selection": request_body.selection.model_dump(mode="json"),
                "operation_id": operation_id,
                "job_id": job_id,
                "recall_id": pack.recall_id,
                "trace_id": trace_id,
                "evidence_id": stage.evidence_id,
                "pack_hash": sha256(pack.model_dump_json().encode()).hexdigest(),
            }
        )

    # Legal maintenance views confirm all five exact fixture bodies/Ready receipts.
    for owner in (*case.memories, foreign):
        maint = RecallHTTP(client, os.environ[owner.maintainer_env])
        me = maint.get("/p3/auth/me")
        assert me.status_code == 200 and Permission.READ.value in me.json()["permissions"]
        snapshot_response = maint.get(f"/p3/remember/{owner.memory.memory_id}")
        assert snapshot_response.status_code == 200
        snapshot = MemorySnapshot.model_validate(snapshot_response.json())
        assert (
            snapshot.ref == owner.memory
            and snapshot.kind == "working"
            and snapshot.status == "active"
        )
        assert (
            snapshot.projection_state == "ready" and snapshot.model_space == deployment.model_space
        )
        assert snapshot.content == owner.text and snapshot.content_hash == owner.body_hash
        body_response = client.post(
            "/p3/remember/body",
            json=owner.memory.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {maint.maintainer}"},
        )
        assert body_response.status_code == 200
        body = FullBodyReadResult.model_validate(body_response.json())
        assert body.outcome == "read" and body.memory == owner.memory and body.content == owner.text
        assert (
            body.sources == owner.sources and body.guard is not None and body.location is not None
        )
        assert body.guard.body_hash == body.location.content_hash == owner.body_hash
        assert body.location.generation == owner.body_generation

    for index, owner in enumerate(case.memories):
        payload = deployment.request.model_dump(mode="json")
        payload["selection"] = {
            "session_id": owner.memory.scope.session_id,
            "task_id": owner.memory.scope.task_id,
        }
        execute(RecallRequest.model_validate(payload), owner.memory, f"control-{index}")

    unknown_empty = variant == "empty" and case.empty_contract is None
    if unknown_empty:
        evidence.blocked(
            "blocked_requirement", "Q01/target empty-selection extent is not established"
        )
    if variant == "omitted":
        # Omission is not rewritten to {}: current RecallRequest requires selection.
        assert RecallRequest.model_fields["selection"].is_required()
        payload = deployment.request.model_dump(mode="json")
        del payload["selection"]
        guard(deployment.request.selection)
        operation_id = f"selection-{deployment.run_id}-omitted"
        response = client.post(
            "/p3/recall",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "X-Operation-ID": operation_id},
        )
        assert response.status_code == 422
        assert any(
            error["loc"] == ["body", "selection"] and error["type"] == "missing"
            for error in response.json()["detail"]
        )
        assert not response.headers.get("X-P3-Job-ID") and not response.headers.get("Location")
        lookup = harness.get(
            f"/p3/operation-requests/{operation_id}", token, params={"kind": "recall.execute"}
        )
        assert lookup.status_code == 200
        original = OperationLookup.model_validate(lookup.json())
        assert (
            original.state == "unconfirmed"
            and original.job_id is None
            and original.input_hash is None
        )
        assert original.workflow_id is None and original.http_request is None
        evidence.data["omitted_contract"] = "RecallRequest.selection-required/HTTP-422"
    else:
        for index, key in enumerate(VARIANT_STEPS[variant]):
            payload = deployment.request.model_dump(mode="json")
            payload["selection"] = case.selector(key).model_dump(mode="json")
            expected = (
                (case.empty_contract.expected_ref if case.empty_contract else None)
                if key == "empty"
                else case.expected_refs[key]
            )
            execute(
                RecallRequest.model_validate(payload),
                expected,
                f"step-{index}-{key}",
                pressure=index == 0 and expected is not None,
            )
    # Revisit every original durable result with its original selection after changes.
    for selection, recall_id, job_id, result in reversed(original_results):
        guard(selection)
        for path in (f"/p3/recalls/{recall_id}/result", f"/p3/operations/{job_id}/result"):
            response = harness.get(path, token)
            assert response.status_code == 200 and response.json() == result
    evidence.data["http_checks"] = "safety_only" if unknown_empty else "completed"
    if unknown_empty:
        pytest.fail(
            "blocked_requirement: Q01/target empty-selection extent unknown; "
            "only no-overreach checked"
        )
