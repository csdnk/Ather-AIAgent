"""AET-29 / RC-AUTH-11 real tenant lifecycle with original-result observations."""

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
from aether_agent_memory.remember.contracts.models import MemorySnapshot
from aether_agent_memory.runtime.contracts.models import TaskOperationView
from recall_authorization_support import RecallHTTP, SafeEvidence, assert_denied
from recall_forged_authorization_support import assert_no_index_disclosure
from recall_shared_read_support import SavedSharedPack, external_path
from recall_shared_revoke_support import pack_hash, wait_receipt
from recall_tenant_isolation_support import (
    assert_isolated_pack,
    assert_stage_isolation,
    load_stage_observation,
)
from recall_tenant_lifecycle_support import (
    VARIANTS,
    LifecyclePhaseObservation,
    LifecycleStage,
    OriginalLifecyclePack,
    TenantLifecycleCase,
    assert_phase_denial,
    transition_tenant,
)

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
def lifecycle_target(request):
    variant = request.node.callspec.params["variant"]
    evidence = SafeEvidence("RC-AUTH-11", variant)
    evidence.data.update(lane="live-http", acceptance="not_evaluated", http_checks="not_run")
    directory = (
        external_path(
            Path(
                os.environ.get(
                    "P3_RECALL_EVIDENCE_DIR",
                    str(Path(gettempdir()) / "aether-workspace-support/AET-29"),
                )
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(reason):
        evidence.blocked("blocked_fixture", reason)
        pytest.fail("blocked_fixture: " + reason)

    path = os.environ.get("P3_AUTH11_FIXTURE_FILE")
    if not path or not Path(path).is_file():
        blocked("maintainer-prepared P3_AUTH11_FIXTURE_FILE unavailable")
    manifest = json.loads(external_path(Path(path)).read_text())
    if variant not in manifest["cases"]:
        blocked("independent tenant lifecycle variant " + variant + " unavailable")
    cases = {
        name: TenantLifecycleCase.model_validate(raw) for name, raw in manifest["cases"].items()
    }
    case = cases[variant]
    if not case.q18_capability_id:
        blocked("Q18 tenant state change and identity reload observations unavailable")
    tenants = [actor.memory.scope.tenant_id for value in cases.values() for actor in value.actors]
    assert len(set(tenants)) == len(tenants) and len({c.run_id for c in cases.values()}) == len(
        cases
    )
    for field in (
        "control_directory",
        "receipt_export",
        "phase_export",
        "success_directory",
        "maintenance_export",
    ):
        assert len({getattr(c, field).resolve() for c in cases.values()}) == len(cases)
    url = urlsplit(manifest["base_url"])
    assert (
        url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
    )
    assert url.path in {"", "/"} and not url.query and not url.fragment
    required = {
        case.operator_env,
        case.new_credential_env,
        *(a.credential_env for a in case.actors),
        *(a.maintainer_env for a in case.actors),
    }
    if any(not os.environ.get(key) for key in required):
        blocked("old/new/control/scoped-maintenance credentials unavailable")
    own, control = case.actors
    old_token, new_token, control_token = (
        os.environ[key]
        for key in (own.credential_env, case.new_credential_env, control.credential_env)
    )
    assert len({old_token, new_token, control_token}) == 3
    assert not {old_token, new_token, control_token}.intersection(
        {os.environ[case.operator_env], *(os.environ[a.maintainer_env] for a in case.actors)}
    )
    visible = {old_token: True, new_token: False, control_token: True}

    def inspect(response):
        token = response.request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in visible:
            return
        response.read()
        assert response.status_code != 304, "old HTTP cache cannot revive disabled authorization"
        allowed_actor = control if token == control_token else own
        private = tuple(
            atom
            for actor in case.actors
            if not visible[token] or actor.name != allowed_actor.name
            for atom in (actor.memory.memory_id, actor.text)
        )
        assert_no_index_disclosure(response.json(), private)
        assert_no_index_disclosure(dict(response.headers), private)
        evidence.response(response.request.method, response.request.url.path, response)

    evidence.data.update(
        run_id=case.run_id,
        source_sha=case.source_sha,
        image_digest=case.image_digest,
        config_hash=case.configuration.config_hash,
        backend_binding=case.backend_binding,
        model_binding=case.model_binding,
        q18_capability_id=case.q18_capability_id,
        old_epoch=case.old_principal.auth_epoch,
        new_epoch=case.new_principal.auth_epoch,
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
                blocked("Ready barrier unavailable")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield client, case, evidence, visible


@pytest.mark.parametrize("variant", VARIANTS)
def test_rc_auth_11_real_tenant_cycle_never_revives_old_credentials_or_epoch(
    variant, lifecycle_target
):
    client, case, evidence, visible = lifecycle_target
    own, control = case.actors
    operator = RecallHTTP(client, os.environ[case.operator_env])
    me = operator.get("/p3/auth/me")
    assert me.status_code == 200
    observer = me.json()["principal_id"]
    configuration = operator.get("/p3/configuration")
    assert (
        configuration.status_code == 200
        and configuration.json() == case.configuration.model_dump(mode="json")
    )
    old_token, new_token = os.environ[own.credential_env], os.environ[case.new_credential_env]
    old_hash, new_hash = (
        sha256(old_token.encode()).hexdigest(),
        sha256(new_token.encode()).hexdigest(),
    )

    def identity(token, principal):
        response = RecallHTTP(client, token).get("/p3/auth/me")
        assert (
            response.status_code == 200
            and response.json()["principal_id"] == principal.principal_id
        )
        assert response.json()["scope"] == principal.home_scope.model_dump(mode="json")
        assert set(response.json()["permissions"]) == {p.value for p in principal.permissions}

    identity(old_token, case.old_principal)
    identity(os.environ[control.credential_env], case.control_principal)
    # The future credential must not already be a statically active second identity.
    assert_denied(RecallHTTP(client, new_token).get("/p3/auth/me"), 401, "UNAUTHENTICATED")
    for actor in case.actors:
        maintainer = RecallHTTP(client, os.environ[actor.maintainer_env])
        response = maintainer.get(f"/p3/remember/{actor.memory.memory_id}")
        assert response.status_code == 200
        snapshot = MemorySnapshot.model_validate(response.json())
        assert snapshot.ref == actor.memory and snapshot.content == actor.text
        assert snapshot.kind == "working" and snapshot.status == "active"
        assert snapshot.projection_state == "ready" and snapshot.content_hash == actor.body_hash
        assert snapshot.model_space == case.model_space
        proof = maintainer.verify_body(actor.memory.model_dump(mode="json"), actor.text)
        assert proof["generation"] == actor.body_generation
    event_ids = set()

    def successful(actor, principal, token, label, revision):
        operation_id = f"lifecycle-{case.run_id}-{label}"
        harness = RecallHTTP(client, token)
        result, job_id = harness.command(
            "/p3/recall", case.request.model_dump(mode="json"), operation_id, token
        )
        foreign = next(a for a in case.actors if a.name != actor.name)
        pack = assert_isolated_pack(
            result, actor.memory.model_dump(mode="json"), actor.text, (foreign.text,)
        )
        assert [item.sources for group in pack.groups for item in group.items] == [actor.sources]
        admin = operator.get(f"/p3/admin/tasks/{job_id}")
        if admin.status_code != 200:
            raise RuntimeError("blocked_fixture: legal original task trace unavailable")
        trace = admin.json()["traces"]["trace_id"]
        stage = load_stage_observation(
            case.maintenance_export, job_id, observation_type=LifecycleStage
        )
        assert isinstance(stage, LifecycleStage)
        ids = assert_stage_isolation(
            stage, case, actor, operation_id, job_id, pack.recall_id, trace, observer
        )
        assert not ids.intersection(event_ids)
        event_ids.update(ids)
        assert stage.principal == principal and stage.trusted_context.principal == principal
        assert (stage.trusted_context.operation_id, stage.trusted_context.trace_id) == (
            operation_id,
            trace,
        )
        assert stage.identity_revision == revision
        qualification = stage.qualification
        assert (
            qualification.decision == "allowed"
            and qualification.target == stage.candidates[0].target
        )
        assert qualification.guard is not None and qualification.manifest is not None
        assert qualification.guard.authorization_epoch == principal.auth_epoch
        for event in stage.events:
            assert event.initiator_auth_epoch == principal.auth_epoch
        record_response = harness.get(f"/p3/recalls/{pack.recall_id}")
        job_response = harness.get(f"/p3/operations/{job_id}")
        assert record_response.status_code == job_response.status_code == 200
        record, job = (
            RecallRecord.model_validate(record_response.json()),
            TaskOperationView.model_validate(job_response.json()),
        )
        assert (
            record.state == "completed" and record.result_available and record.scope == pack.scope
        )
        assert job.state == "succeeded" and job.initiator_auth_epoch == principal.auth_epoch
        assert record.recall_id == pack.recall_id
        assert job.task_id == job_id and job.idempotency_key == operation_id
        assert job.initiator_id == principal.principal_id and job.result_ref is not None
        assert job.result_ref.scope == job.subject.scope == pack.scope
        for path in (f"/p3/recalls/{pack.recall_id}/result", f"/p3/operations/{job_id}/result"):
            response = harness.get(path)
            assert response.status_code == 200 and response.json() == pack.model_dump(mode="json")
        saved = SavedSharedPack(
            operation_id=operation_id,
            job_id=job_id,
            trace_id=trace,
            grant_evidence_id=None,
            pack=pack,
        )
        directory = external_path(case.success_directory)
        directory.mkdir(parents=True, exist_ok=True)
        with os.fdopen(
            os.open(
                directory / (operation_id + ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            ),
            "w",
        ) as stream:
            stream.write(saved.model_dump_json())
        evidence.data.setdefault("operations", []).append(
            {
                "operation_id": operation_id,
                "job_id": job_id,
                "recall_id": pack.recall_id,
                "trace_id": trace,
                "epoch": principal.auth_epoch,
                "identity_revision": revision,
            }
        )
        return OriginalLifecyclePack(saved=saved, record=record, job=job, stage=stage)

    try:
        original = successful(
            own, case.old_principal, old_token, "original", case.initial_identity_revision
        )
        successful(
            control,
            case.control_principal,
            os.environ[control.credential_env],
            "control-before",
            case.initial_identity_revision,
        )
        context = original.stage.trusted_context
        receipt = transition_tenant(case, "enabled", None, context, old_hash, observer)
        evidence.data.update(
            original_pack_hash=pack_hash(original.saved),
            original_guard=original.stage.qualification.guard.model_dump(mode="json"),
        )
        for phase in ("disabled", "reenabled"):
            receipt = transition_tenant(
                case,
                phase,
                receipt,
                context,
                new_hash if phase == "reenabled" else old_hash,
                observer,
            )
            visible[old_token] = False
            if phase == "reenabled":
                visible[new_token] = True
            evidence.data.setdefault("transitions", []).append(
                {
                    "phase": phase,
                    "evidence_id": receipt.evidence_id,
                    "revision": receipt.identity_revision,
                    "submitted_at": receipt.submitted_at,
                    "committed_at": receipt.committed_at,
                    "effective_at": receipt.effective_at,
                    "epoch": receipt.current_identity.principal.auth_epoch,
                    "reload_instances": [r.instance_id for r in receipt.reloads],
                }
            )
            before = operator.recall_tasks()
            operation_id = f"lifecycle-{case.run_id}-denied-{phase}"
            headers = {"Authorization": f"Bearer {old_token}", "X-Operation-ID": operation_id}
            status, code = (403, "FORBIDDEN") if phase == "disabled" else (401, "UNAUTHENTICATED")
            requests = {}

            def denied(
                response,
                request_operation=None,
                *,
                expected_status=status,
                expected_code=code,
                observed_requests=requests,
            ):
                assert_denied(response, expected_status, expected_code)
                assert not response.headers.get("Location") and not response.headers.get(
                    "X-P3-Job-ID"
                )
                request_id = response.headers.get("X-Request-ID")
                assert request_id and request_id not in observed_requests
                observed_requests[request_id] = (
                    response.request.method,
                    response.request.url.path,
                    request_operation,
                )

            denied(
                client.post(
                    "/p3/recall", json=case.request.model_dump(mode="json"), headers=headers
                ),
                operation_id,
            )
            result_path = (
                f"/p3/recalls/{original.saved.pack.recall_id}/result"
                if variant == "recall-result"
                else f"/p3/operations/{original.saved.job_id}/result"
            )
            for path in (
                "/p3/auth/me",
                f"/p3/recalls/{original.saved.pack.recall_id}",
                result_path,
                f"/p3/operations/{original.saved.job_id}",
            ):
                denied(client.get(path, headers={"Authorization": f"Bearer {old_token}"}))
            after = operator.recall_tasks()
            added = set(after) - set(before)
            for job_id in added:
                assert after[job_id]["idempotency_key"] == operation_id
                terminal = operator.poll_job(job_id)
                assert (
                    terminal["state"] in {"failed", "cancelled"} and terminal["error_code"] == code
                )
            observation = wait_receipt(
                case.phase_export,
                "control_operation_id",
                receipt.control_operation_id,
                LifecyclePhaseObservation,
            )
            assert_phase_denial(
                observation, case, receipt, original, requests, old_hash, added, observer
            )
            evidence.data.setdefault("phase_evidence_ids", []).append(observation.evidence_id)
            successful(
                control,
                case.control_principal,
                os.environ[control.credential_env],
                "control-" + phase,
                receipt.identity_revision,
            )
        identity(new_token, case.new_principal)
        current = successful(
            own, case.new_principal, new_token, "fresh-epoch", receipt.identity_revision
        )
        assert current.saved.operation_id != original.saved.operation_id
        assert (
            current.stage.qualification.guard.authorization_epoch == case.new_principal.auth_epoch
        )
        # A fresh epoch retains current scope restrictions; tenant name grants no foreign access.
        assert_denied(
            RecallHTTP(client, new_token).get(f"/p3/remember/{control.memory.memory_id}"),
            403,
            "FORBIDDEN",
        )
        evidence.data["http_checks"] = "completed"
    except RuntimeError as exc:
        evidence.blocked("blocked_fixture", str(exc))
        raise
