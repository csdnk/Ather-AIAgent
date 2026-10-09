"""AET-24: live RC-AUTH-04/05; fixtures and receipts are maintained externally."""

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
from recall_authorization_support import RecallHTTP, SafeEvidence, assert_denied
from recall_scope_isolation_support import (
    ScopeIsolationCase,
    ScopeStageObservation,
    assert_no_implicit_sharing,
    assert_scope_pressure,
)
from recall_tenant_isolation_support import (
    assert_isolated_pack,
    assert_public_isolation,
    assert_stage_isolation,
    load_stage_observation,
)

pytestmark = [pytest.mark.integration, pytest.mark.p0]
VARIANTS = tuple(
    (dimension, lower)
    for dimension in ("application_id", "user_id", "agent_id")
    for lower in ("U01", "U04")
)


@pytest.fixture
def scope_target(request):
    dimension, lower = (
        request.node.callspec.params["dimension"],
        request.node.callspec.params["lower"],
    )
    variant = f"{dimension}-{lower}-lower"
    case_id = "RC-AUTH-04" if dimension == "application_id" else "RC-AUTH-05"
    evidence = SafeEvidence(case_id, variant)
    evidence.data.update(acceptance="not_evaluated", http_checks="not_run")
    directory = (
        Path(
            os.environ.get(
                "P3_RECALL_EVIDENCE_DIR",
                str(Path(gettempdir()) / "aether-workspace-support/AET-24"),
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(reason):
        evidence.blocked("blocked_fixture", reason)
        pytest.fail("blocked_fixture: " + reason)

    path = os.environ.get("P3_AUTH0405_FIXTURE_FILE")
    if not path or not Path(path).is_file():
        blocked("maintainer-prepared P3_AUTH0405_FIXTURE_FILE unavailable")
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if variant not in manifest["cases"]:
        blocked("independent scope variant " + variant + " unavailable")
    if not manifest["cases"][variant].get("authorization_evidence_id"):
        evidence.blocked(
            "blocked_requirement", "authoritative no-share qualification policy evidence"
        )
        pytest.fail("blocked_requirement: authoritative no-share qualification policy unavailable")
    cases = {
        name: ScopeIsolationCase.model_validate(value) for name, value in manifest["cases"].items()
    }
    case = cases[variant]
    assert case.dimension == dimension
    # One independent tenant per variant, shared by its two single-dimension actors.
    tenants = [value.actors[0].memory.scope.tenant_id for value in cases.values()]
    assert len(set(tenants)) == len(cases), "variants must own independent tenant/data copies"
    assert len({value.run_id for value in cases.values()}) == len(cases)
    url = urlsplit(manifest["base_url"])
    assert (
        url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
    )
    assert url.path in {"", "/"} and not url.query and not url.fragment
    required = {case.operator_env} | {
        env for actor in case.actors for env in (actor.credential_env, actor.maintainer_env)
    }
    if any(not os.environ.get(env) for env in required):
        blocked("reader/scoped maintainer/platform operator credentials unavailable")
    reader_tokens = {os.environ[actor.credential_env] for actor in case.actors}
    maintenance_tokens = {os.environ[case.operator_env]} | {
        os.environ[actor.maintainer_env] for actor in case.actors
    }
    assert len(reader_tokens) == 2 and not reader_tokens.intersection(maintenance_tokens)
    forbidden = {}
    for actor in case.actors:
        other = next(a for a in case.actors if a.name != actor.name)
        own_sources = {source.source_id for source in actor.sources}
        forbidden[os.environ[actor.credential_env]] = [
            other.memory.memory_id,
            other.text,
            *(source.source_id for source in other.sources if source.source_id not in own_sources),
        ]
        # The foreign selector itself is caller supplied; memory/source IDs and body
        # are private canaries. Do not confuse echoing a requested scope with discovery.
    evidence.data.update(
        lane="live-http-maintained-F4",
        run_id=case.run_id,
        dimension=dimension,
        source_sha=case.source_sha,
        image_digest=case.image_digest,
        config_hash=case.configuration.config_hash,
        server_candidate_limit=1,
        backend_binding=case.backend_binding,
        model_binding=case.model_binding,
        authorization_evidence_id=case.authorization_evidence_id,
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

    def inspect(response):
        credential = response.request.headers.get("Authorization", "").removeprefix("Bearer ")
        if credential in forbidden:
            response.read()
            assert_public_isolation(response.json(), tuple(forbidden[credential]))
            assert_public_isolation(dict(response.headers), tuple(forbidden[credential]))
            if response.status_code >= 400:
                error = ErrorResponse.model_validate(response.json())
                assert error.message == error.code.value.lower(), "free-form error content leak"
            evidence.response(response.request.method, response.request.url.path, response)

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


@pytest.mark.parametrize("dimension,lower", VARIANTS)
def test_rc_auth_04_05_selection_only_narrows_application_user_agent_scope(
    dimension, lower, scope_target
):
    client, case, evidence, forbidden = scope_target
    operator = RecallHTTP(client, os.environ[case.operator_env])
    admin_me = operator.get("/p3/auth/me")
    assert admin_me.status_code == 200
    configuration = operator.get("/p3/configuration")
    assert configuration.status_code == 200
    assert configuration.json() == case.configuration.model_dump(mode="json")
    controls = {}
    event_ids = set()
    for actor in case.actors:
        token = os.environ[actor.credential_env]
        harness = RecallHTTP(client, os.environ[actor.maintainer_env])
        me = harness.get("/p3/auth/me", token)
        assert me.status_code == 200 and me.json()["principal_id"] == actor.name
        assert Permission.READ.value in me.json()["permissions"]
        # Session/task are also bound: no accidental second isolation dimension.
        assert me.json()["scope"] == actor.memory.scope.model_dump(mode="json")
        maintainer = harness.get("/p3/auth/me")
        assert (
            maintainer.status_code == 200
            and Permission.READ.value in maintainer.json()["permissions"]
        )
        snapshot = harness.get(f"/p3/remember/{actor.memory.memory_id}")
        assert snapshot.status_code == 200
        memory = MemorySnapshot.model_validate(snapshot.json())
        assert memory.ref == actor.memory and memory.kind == "working" and memory.status == "active"
        assert memory.projection_state == "ready" and memory.model_space == case.model_space
        assert memory.content == actor.text and memory.content_hash == actor.body_hash
        body_response = client.post(
            "/p3/remember/body",
            json=actor.memory.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {harness.maintainer}"},
        )
        assert body_response.status_code == 200
        body = FullBodyReadResult.model_validate(body_response.json())
        assert body.outcome == "read" and body.memory == actor.memory and body.content == actor.text
        assert (
            body.sources == actor.sources and body.guard is not None and body.location is not None
        )
        assert body.guard.body_hash == body.location.content_hash == actor.body_hash
        assert body.location.generation == actor.body_generation
        # Both legal controls must finish before any cross-scope attack.
        operation_id = f"scope-{case.run_id}-{actor.name}-baseline"
        result, job_id = harness.command(
            "/p3/recall", case.request.model_dump(mode="json"), operation_id, token
        )
        other = next(a for a in case.actors if a.name != actor.name)
        control = assert_isolated_pack(
            result, actor.memory.model_dump(mode="json"), actor.text, (other.text,)
        )
        assert tuple(item.sources for group in control.groups for item in group.items) == (
            actor.sources,
        )
        controls[actor.name] = (result, job_id, operation_id)

    assert controls["U01"][1] != controls["U04"][1]
    for actor in case.actors:
        other = next(a for a in case.actors if a.name != actor.name)
        foreign_result, foreign_job, _ = controls[other.name]
        forbidden[os.environ[actor.credential_env]].extend(
            [foreign_job, foreign_result["recall_id"]]
        )

    for actor in case.actors:
        other = next(a for a in case.actors if a.name != actor.name)
        token = os.environ[actor.credential_env]
        harness = RecallHTTP(client, os.environ[actor.maintainer_env])
        baseline, baseline_job, baseline_op = controls[actor.name]
        selected = case.request.model_dump(mode="json")
        selected["selection"][dimension] = getattr(actor.memory.scope, dimension)
        explicit = RecallRequest.model_validate(selected)
        for mode, request_body in (("baseline", case.request), ("explicit-own", explicit)):
            if mode == "baseline":
                result, job_id, operation_id = baseline, baseline_job, baseline_op
            else:
                operation_id = f"scope-{case.run_id}-{actor.name}-{mode}"
                result, job_id = harness.command(
                    "/p3/recall", request_body.model_dump(mode="json"), operation_id, token
                )
            pack = assert_isolated_pack(
                result, actor.memory.model_dump(mode="json"), actor.text, (other.text,)
            )
            assert tuple(item.sources for group in pack.groups for item in group.items) == (
                actor.sources,
            )
            record_response = harness.get(f"/p3/recalls/{pack.recall_id}", token)
            assert record_response.status_code == 200
            record = RecallRecord.model_validate(record_response.json())
            assert (
                record.state == "completed"
                and record.result_available
                and record.scope == actor.memory.scope
            )
            job_response = harness.get(f"/p3/operations/{job_id}", token)
            assert job_response.status_code == 200
            job = TaskOperationView.model_validate(job_response.json())
            assert job.state == "succeeded" and job.initiator_id == actor.name
            assert job.subject.scope == actor.memory.scope
            assert job.result_ref is not None and job.result_ref.scope == actor.memory.scope
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
                    case.maintenance_export, job_id, observation_type=ScopeStageObservation
                )
            except RuntimeError:
                evidence.blocked(
                    "blocked_fixture", "authorized original-job stage receipt unavailable"
                )
                raise
            assert isinstance(stage, ScopeStageObservation)
            assert_no_implicit_sharing(stage, case)
            observed = assert_stage_isolation(
                stage,
                case.model_copy(update={"request": request_body}),
                actor,
                operation_id,
                job_id,
                pack.recall_id,
                trace_id,
                admin_me.json()["principal_id"],
            )
            assert not event_ids.intersection(observed), "cross-operation access event collision"
            event_ids.update(observed)
            if actor.name == lower:
                assert_scope_pressure(
                    [h.model_dump(mode="json") for h in stage.unfiltered_probe], case, actor
                )
            evidence.data.setdefault("operations", []).append(
                {
                    "actor": actor.name,
                    "mode": mode,
                    "operation_id": operation_id,
                    "job_id": job_id,
                    "recall_id": pack.recall_id,
                    "trace_id": trace_id,
                    "evidence_id": stage.evidence_id,
                    "pack_hash": sha256(pack.model_dump_json().encode()).hexdigest(),
                }
            )

        attack = explicit.model_dump(mode="json")
        attack["selection"][dimension] = getattr(other.memory.scope, dimension)
        # Same query and every other field; the sole change is the selected dimension.
        attack = RecallRequest.model_validate(attack).model_dump(mode="json")
        attack_op = f"scope-{case.run_id}-{actor.name}-foreign-selection"
        denied = client.post(
            "/p3/recall",
            json=attack,
            headers={"Authorization": f"Bearer {token}", "X-Operation-ID": attack_op},
        )
        assert_denied(denied, 403, "FORBIDDEN")
        assert not denied.headers.get("X-P3-Job-ID") and not denied.headers.get("Location")
        lookup = harness.get(
            f"/p3/operation-requests/{attack_op}", token, params={"kind": "recall.execute"}
        )
        assert lookup.status_code == 200
        unconfirmed = OperationLookup.model_validate(lookup.json())
        assert unconfirmed.state == "unconfirmed" and unconfirmed.job_id is None
        assert (
            unconfirmed.input_hash is None
            and unconfirmed.workflow_id is None
            and unconfirmed.http_request is None
        )
        other_result, other_job, _ = controls[other.name]
        for path in (
            f"/p3/recalls/{other_result['recall_id']}",
            f"/p3/recalls/{other_result['recall_id']}/result",
            f"/p3/operations/{other_job}",
            f"/p3/operations/{other_job}/result",
        ):
            assert_denied(harness.get(path, token), 403, "FORBIDDEN")
        # The failed expansion must not contaminate the original durable result.
        final = harness.get(f"/p3/operations/{baseline_job}/result", token)
        assert final.status_code == 200 and final.json() == baseline
        evidence.data.setdefault("denials", []).append(
            {
                "actor": actor.name,
                "operation_id": attack_op,
                "code": "FORBIDDEN",
                "binding": "unconfirmed",
            }
        )
    evidence.data["http_checks"] = "completed"
