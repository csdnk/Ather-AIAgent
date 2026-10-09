"""AET-26 live HTTP: untrusted request/index claims cannot grant body READ."""

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

from aether_agent_memory.recall.contracts.models import RecallRecord
from aether_agent_memory.remember.contracts.foundation import FullBodyReadResult
from aether_agent_memory.remember.contracts.models import MemorySnapshot
from aether_agent_memory.runtime.contracts.models import (
    ErrorResponse,
    Permission,
    TaskOperationView,
)
from aether_agent_memory.runtime.contracts.operation_lookup import OperationLookup
from recall_authorization_support import RecallHTTP, SafeEvidence, assert_denied
from recall_forged_authorization_support import (
    INDEX_VARIANTS,
    REQUEST_VARIANTS,
    ForgeryCase,
    ForgeryStageObservation,
    assert_authority_veto,
    assert_no_index_disclosure,
    assert_trusted_subject,
    forged_request,
)
from recall_tenant_isolation_support import (
    assert_isolated_pack,
    assert_stage_isolation,
    assert_top1_pressure,
    load_stage_observation,
)

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
def forgery_target(request):
    variant = request.node.callspec.params["variant"]
    case_id = "RC-AUTH-08" if variant in INDEX_VARIANTS else "RC-AUTH-07"
    evidence = SafeEvidence(case_id, variant)
    evidence.data.update(acceptance="not_evaluated", http_checks="not_run")
    directory = (
        Path(
            os.environ.get(
                "P3_RECALL_EVIDENCE_DIR",
                str(Path(gettempdir()) / "aether-workspace-support/AET-26"),
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(category, reason):
        evidence.blocked(category, reason)
        pytest.fail(category + ": " + reason)

    path = os.environ.get("P3_AUTH0708_FIXTURE_FILE")
    if not path or not Path(path).is_file():
        blocked("blocked_fixture", "maintainer-prepared P3_AUTH0708_FIXTURE_FILE unavailable")
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if variant not in manifest["cases"]:
        blocked("blocked_fixture", "independent forgery variant " + variant + " unavailable")
    if not manifest["cases"][variant].get("q06_mapping_id"):
        blocked(
            "blocked_requirement", "Q06 qualify/load_bodies/rerank evidence mapping unavailable"
        )
    if variant in INDEX_VARIANTS and not manifest["cases"][variant].get("q18_capability_id"):
        blocked(
            "blocked_fixture", "Q18 legal operation-bound index injection capability unavailable"
        )
    cases = {name: ForgeryCase.model_validate(value) for name, value in manifest["cases"].items()}
    case = cases[variant]
    tenants = [a.memory.scope.tenant_id for value in cases.values() for a in value.actors]
    assert len(set(tenants)) == len(tenants), "variants must own independent data/scopes"
    assert len({value.run_id for value in cases.values()}) == len(cases)
    url = urlsplit(manifest["base_url"])
    assert (
        url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
    )
    assert url.path in {"", "/"} and not url.query and not url.fragment
    required = {case.operator_env} | {
        name for a in case.actors for name in (a.credential_env, a.maintainer_env)
    }
    if any(not os.environ.get(name) for name in required):
        blocked(
            "blocked_fixture", "reader/scoped maintainer/platform operator credentials unavailable"
        )
    token = os.environ[case.actors[0].credential_env]
    assert token != os.environ[case.actors[1].credential_env]
    assert token not in {
        os.environ[case.operator_env],
        *(os.environ[a.maintainer_env] for a in case.actors),
    }
    private = (
        case.actors[1].memory.memory_id,
        case.actors[1].text,
        *(
            s.source_id
            for s in case.actors[1].sources
            if s.source_id not in {x.source_id for x in case.actors[0].sources}
        ),
    )

    def inspect(response):
        credential = response.request.headers.get("Authorization", "").removeprefix("Bearer ")
        if credential == token:
            response.read()
            assert_no_index_disclosure(response.json(), private)
            assert_no_index_disclosure(dict(response.headers), private)
            if response.status_code >= 400 and "code" in response.json():
                error = ErrorResponse.model_validate(response.json())
                assert error.message == error.code.value.lower()
            evidence.response(response.request.method, response.request.url.path, response)

    evidence.data.update(
        lane="live-http-controlled-index-fault"
        if variant in INDEX_VARIANTS
        else "live-http-maintained-F4",
        run_id=case.run_id,
        source_sha=case.source_sha,
        image_digest=case.image_digest,
        config_hash=case.configuration.config_hash,
        server_candidate_limit=1,
        backend_binding=case.backend_binding,
        model_binding=case.model_binding,
        q06_mapping_id=case.q06_mapping_id,
        q18_capability_id=case.q18_capability_id,
        memories=[
            {
                "ref": a.memory.model_dump(mode="json"),
                "body_hash": a.body_hash,
                "body_generation": a.body_generation,
                "projection_generation": a.projection_generation,
            }
            for a in case.actors
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
                blocked("blocked_fixture", "target Ready barrier unavailable")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield client, case, evidence


@pytest.mark.parametrize("variant", (*REQUEST_VARIANTS, *INDEX_VARIANTS))
def test_rc_auth_07_08_untrusted_claims_never_authorize_body_or_model_input(
    variant, forgery_target
):
    client, case, evidence = forgery_target
    owner, foreign = case.actors
    token = os.environ[owner.credential_env]
    harness = RecallHTTP(client, os.environ[owner.maintainer_env])
    operator = RecallHTTP(client, os.environ[case.operator_env])
    operator_me = operator.get("/p3/auth/me")
    assert operator_me.status_code == 200
    configuration = operator.get("/p3/configuration")
    assert (
        configuration.status_code == 200
        and configuration.json() == case.configuration.model_dump(mode="json")
    )
    identity = harness.get("/p3/auth/me", token)
    assert identity.status_code == 200 and identity.json()["principal_id"] == owner.name
    assert identity.json()["scope"] == case.principal.home_scope.model_dump(mode="json")
    assert Permission.READ.value in identity.json()["permissions"]
    events, originals = set(), []

    def execute(label, injected=False):
        operation_id = f"forgery-{case.run_id}-{label}"
        result, job_id = harness.command(
            "/p3/recall", case.request.model_dump(mode="json"), operation_id, token
        )
        pack = assert_isolated_pack(
            result, owner.memory.model_dump(mode="json"), owner.text, (foreign.text,)
        )
        assert tuple(item.sources for group in pack.groups for item in group.items) == (
            owner.sources,
        )
        record_response = harness.get(f"/p3/recalls/{pack.recall_id}", token)
        assert record_response.status_code == 200
        record = RecallRecord.model_validate(record_response.json())
        assert (
            record.state == "completed"
            and record.result_available
            and record.scope == owner.memory.scope
        )
        job_response = harness.get(f"/p3/operations/{job_id}", token)
        assert job_response.status_code == 200
        job = TaskOperationView.model_validate(job_response.json())
        assert (
            job.state == "succeeded"
            and job.initiator_id == owner.name
            and job.subject.scope == owner.memory.scope
        )
        assert job.result_ref is not None and job.result_ref.scope == owner.memory.scope
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
                case.maintenance_export, job_id, observation_type=ForgeryStageObservation
            )
        except RuntimeError:
            evidence.blocked(
                "blocked_fixture", "original-job qualification/body/model receipt unavailable"
            )
            raise
        assert isinstance(stage, ForgeryStageObservation)
        current = assert_stage_isolation(
            stage,
            case,
            owner,
            operation_id,
            job_id,
            pack.recall_id,
            trace_id,
            operator_me.json()["principal_id"],
        )
        assert not events.intersection(current), "cross-operation access event collision"
        events.update(current)
        assert_trusted_subject(stage, case)
        if injected:
            assert_authority_veto(stage, case, variant)
        else:
            assert stage.injection_kind is None, (
                "baseline/follow-up must be outside the injection window"
            )
            assert_top1_pressure(
                [h.model_dump(mode="json") for h in stage.unfiltered_probe],
                owner.memory.model_dump(mode="json"),
                foreign.memory.model_dump(mode="json"),
            )
            for hit in stage.unfiltered_probe:
                actor = next(a for a in case.actors if a.memory == hit.target.memory)
                assert (
                    hit.target.body_hash == actor.body_hash
                    and hit.target.generation == actor.projection_generation
                )
                assert (
                    hit.target.model_space == case.model_space
                    and hit.target.memory_source == "working"
                )
        originals.append((pack.recall_id, job_id, result))
        evidence.data.setdefault("operations", []).append(
            {
                "label": label,
                "operation_id": operation_id,
                "job_id": job_id,
                "recall_id": pack.recall_id,
                "trace_id": trace_id,
                "evidence_id": stage.evidence_id,
                "injection_evidence_id": stage.injection_evidence_id,
                "pack_hash": sha256(pack.model_dump_json().encode()).hexdigest(),
            }
        )

    for actor in case.actors:
        maint = RecallHTTP(client, os.environ[actor.maintainer_env])
        me = maint.get("/p3/auth/me")
        assert me.status_code == 200 and Permission.READ.value in me.json()["permissions"]
        snapshot_response = maint.get(f"/p3/remember/{actor.memory.memory_id}")
        assert snapshot_response.status_code == 200
        snapshot = MemorySnapshot.model_validate(snapshot_response.json())
        assert (
            snapshot.ref == actor.memory
            and snapshot.kind == "working"
            and snapshot.status == "active"
        )
        assert snapshot.projection_state == "ready" and snapshot.model_space == case.model_space
        assert snapshot.content == actor.text and snapshot.content_hash == actor.body_hash
        body_response = client.post(
            "/p3/remember/body",
            json=actor.memory.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {maint.maintainer}"},
        )
        assert body_response.status_code == 200
        body = FullBodyReadResult.model_validate(body_response.json())
        assert body.outcome == "read" and body.memory == actor.memory and body.content == actor.text
        assert (
            body.sources == actor.sources and body.guard is not None and body.location is not None
        )
        assert body.guard.body_hash == body.location.content_hash == actor.body_hash
        assert body.location.generation == actor.body_generation

    execute("baseline")
    if variant in REQUEST_VARIANTS:
        payload, status = forged_request(case, variant)
        operation_id = f"forgery-{case.run_id}-attack"
        denied = client.post(
            "/p3/recall",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "X-Operation-ID": operation_id},
        )
        if status == 403:
            assert_denied(denied, 403, "FORBIDDEN")
        else:
            assert denied.status_code == 422
            location, field = variant.split("-", 1)
            expected_loc = ["body", field] if location == "body" else ["body", "selection", field]
            assert any(
                error["loc"] == expected_loc and error["type"] == "extra_forbidden"
                for error in denied.json()["detail"]
            )
        assert not denied.headers.get("X-P3-Job-ID") and not denied.headers.get("Location")
        lookup = harness.get(
            f"/p3/operation-requests/{operation_id}", token, params={"kind": "recall.execute"}
        )
        assert lookup.status_code == 200
        binding = OperationLookup.model_validate(lookup.json())
        assert (
            binding.state == "unconfirmed" and binding.job_id is None and binding.input_hash is None
        )
        assert binding.workflow_id is None and binding.http_request is None
        evidence.data["attack"] = {
            "operation_id": operation_id,
            "status": status,
            "binding": "unconfirmed",
        }
    else:
        # The external Q18 controller arms only this exact operation at the search
        # return barrier. No guessed HTTP injection route or auth override.
        execute("attack", injected=True)
    after = harness.get("/p3/auth/me", token)
    assert after.status_code == 200
    for field in ("principal_id", "scope", "permissions"):
        assert after.json()[field] == identity.json()[field]
    execute("after")  # Actual trusted principal/home/epoch receipt after the forgery.
    for recall_id, job_id, result in originals:
        for path in (f"/p3/recalls/{recall_id}/result", f"/p3/operations/{job_id}/result"):
            response = harness.get(path, token)
            assert response.status_code == 200 and response.json() == result
    evidence.data["http_checks"] = "completed"
