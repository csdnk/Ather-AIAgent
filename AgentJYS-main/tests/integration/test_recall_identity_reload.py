"""AET-30 / RC-AUTH-12: revoke READ at the witnessed B5 barrier, before final_guard."""

import json
import os
import time
from pathlib import Path
from tempfile import gettempdir
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.remember.contracts.models import MemorySnapshot
from recall_authorization_support import RecallHTTP, SafeEvidence, assert_denied
from recall_forged_authorization_support import assert_no_index_disclosure
from recall_identity_reload_support import (
    ArmedReceipt,
    AssemblyObservation,
    FinalObservation,
    IdentityReloadCase,
    ReadRevocationReceipt,
    assert_assembly,
    assert_final,
    assert_pack,
    assert_revocation,
    cleanup,
    publish,
)
from recall_shared_read_support import external_path
from recall_shared_revoke_support import wait_receipt

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
def identity_reload_target(request):
    evidence = SafeEvidence("RC-AUTH-12", "B5-revoke-READ")
    evidence.data.update(lane="live-http", acceptance="not_evaluated", http_checks="not_run")
    directory = (
        external_path(
            Path(
                os.environ.get(
                    "P3_RECALL_EVIDENCE_DIR",
                    str(Path(gettempdir()) / "aether-workspace-support/AET-30"),
                )
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(category, reason):
        evidence.blocked(category, reason)
        pytest.fail(category + ": " + reason)

    path = os.environ.get("P3_AUTH12_FIXTURE_FILE")
    if not path or not Path(path).is_file():
        blocked("blocked_fixture", "maintainer-prepared P3_AUTH12_FIXTURE_FILE unavailable")
    manifest = json.loads(external_path(Path(path)).read_text())
    case = IdentityReloadCase.model_validate(manifest["case"])
    if not case.b5_capability_id or not case.q18_capability_id:
        blocked("blocked_fixture", "B5/Q18 controlled barrier and identity reload unavailable")
    if not case.q06_capability_id:
        blocked("blocked_requirement", "Q06 final guard/conditional commit/Outbox view unavailable")
    if case.control_directory.exists() and any(case.control_directory.iterdir()):
        blocked("blocked_fixture", "fresh independent control directory required for every run")
    for export in (case.receipt_export, case.final_export, case.maintenance_export):
        if export.exists():
            blocked("blocked_fixture", "fresh independent exports required for every run")
    required = {
        case.operator_env,
        *(a.credential_env for a in case.actors),
        *(a.maintainer_env for a in case.actors),
    }
    if any(not os.environ.get(key) for key in required):
        blocked("blocked_fixture", "ordinary and scoped maintenance credentials unavailable")
    token = os.environ[case.actors[0].credential_env]
    assert token not in {
        os.environ[case.operator_env],
        *(os.environ[a.maintainer_env] for a in case.actors),
    }
    url = urlsplit(manifest["base_url"])
    assert (
        url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
    )
    assert url.path in {"", "/"} and not url.query and not url.fragment
    visible = {"read": True}

    def inspect(response):
        if response.request.headers.get("Authorization") != "Bearer " + token:
            return
        response.read()
        assert response.status_code != 304
        if not visible["read"]:
            atoms = tuple(atom for a in case.actors for atom in (a.memory.memory_id, a.text))
            assert_no_index_disclosure(response.json(), atoms)
            assert_no_index_disclosure(dict(response.headers), atoms)
        evidence.response(response.request.method, response.request.url.path, response)

    evidence.data.update(
        run_id=case.run_id,
        source_sha=case.source_sha,
        image_digest=case.image_digest,
        config_hash=case.configuration.config_hash,
        backend_binding=case.backend_binding,
        model_binding=case.model_binding,
        b5_capability_id=case.b5_capability_id,
        q18_capability_id=case.q18_capability_id,
        q06_capability_id=case.q06_capability_id,
        memories=[
            {
                "ref": a.memory.model_dump(mode="json"),
                "hash": a.body_hash,
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
                blocked("blocked_fixture", "Ready barrier unavailable")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield client, case, evidence, visible


def test_rc_auth_12_read_revoked_after_assembly_blocks_original_commit_and_delivery(
    identity_reload_target,
):
    client, case, evidence, visible = identity_reload_target
    token = os.environ[case.actors[0].credential_env]
    reader = RecallHTTP(client, token)
    operator = RecallHTTP(client, os.environ[case.operator_env])
    me = operator.get("/p3/auth/me")
    assert me.status_code == 200
    observer = me.json()["principal_id"]
    configuration = operator.get("/p3/configuration")
    assert configuration.status_code == 200
    assert configuration.json() == case.configuration.model_dump(mode="json")
    identity = reader.get("/p3/auth/me")
    assert identity.status_code == 200 and identity.json()["principal_id"] == "U01"
    assert identity.json()["scope"] == case.principal.home_scope.model_dump(mode="json")
    assert set(identity.json()["permissions"]) == {p.value for p in case.principal.permissions}
    for actor in case.actors:
        maintainer = RecallHTTP(client, os.environ[actor.maintainer_env])
        response = maintainer.get(f"/p3/remember/{actor.memory.memory_id}")
        assert response.status_code == 200
        snapshot = MemorySnapshot.model_validate(response.json())
        assert snapshot.ref == actor.memory and snapshot.content == actor.text
        assert snapshot.kind == "working" and snapshot.status == "active"
        assert snapshot.projection_state == "ready" and snapshot.model_space == case.model_space
        assert snapshot.content_hash == actor.body_hash
        proof = maintainer.verify_body(actor.memory.model_dump(mode="json"), actor.text)
        assert proof["generation"] == actor.body_generation

    previous_revision = case.initial_identity_revision
    try:
        control_op = "control-" + case.run_id
        pack, control_job = reader.command(
            "/p3/recall", case.request.model_dump(mode="json"), control_op, token
        )
        control = wait_receipt(case.maintenance_export, "job_id", control_job, AssemblyObservation)
        assert_assembly(control, case, observer, control_op, control_job, paused=False)
        assert control.pack is not None
        assert_pack(control.pack, case)
        assert control.pack.model_dump(mode="json") == pack
        operation_id = "inflight-" + case.run_id
        publish(
            case,
            "arm",
            operation_id=operation_id,
            b5_capability_id=case.b5_capability_id,
            position="after_assemble_before_final_guard",
            auto_release_seconds=120,
        )
        armed = wait_receipt(
            case.receipt_export, "control_operation_id", "arm-" + case.run_id, ArmedReceipt
        )
        assert armed.run_id == case.run_id and armed.operation_id == operation_id
        assert armed.b5_capability_id == case.b5_capability_id and armed.armed
        response = client.post(
            "/p3/recall",
            json=case.request.model_dump(mode="json"),
            headers={
                "Authorization": "Bearer " + token,
                "X-Operation-ID": operation_id,
            },
        )
        assert response.status_code in {202, 400}
        if response.status_code == 400:
            assert response.json()["code"] == "REQUEST_IN_PROGRESS"
        job_id = response.headers.get("X-P3-Job-ID") or response.headers.get(
            "Location", ""
        ).removeprefix("/p3/operations/")
        assert job_id and "/" not in job_id and job_id != control_job
        paused = wait_receipt(case.maintenance_export, "job_id", job_id, AssemblyObservation)
        assert_assembly(paused, case, observer, operation_id, job_id, paused=True)
        assert paused.recall_id != control.recall_id
        evidence.data.update(
            operation_id=operation_id,
            job_id=job_id,
            recall_id=paused.recall_id,
            trace_id=paused.trace_id,
            b5_evidence_id=paused.evidence_id,
            barrier_id=paused.barrier_id,
        )
        publish(
            case,
            "revoke",
            operation_id=operation_id,
            job_id=job_id,
            barrier_id=paused.barrier_id,
            b5_evidence_id=paused.evidence_id,
            q18_capability_id=case.q18_capability_id,
            entrypoint="Service.reload_identity",
            principal=case.revoked_principal().model_dump(mode="json"),
            credential_env=case.actors[0].credential_env,
            preserve_other_identities_tenants_grants_jwt=True,
        )
        receipt = wait_receipt(
            case.receipt_export,
            "control_operation_id",
            "revoke-" + case.run_id,
            ReadRevocationReceipt,
        )
        assert_revocation(receipt, paused, case, observer, token)
        previous_revision = receipt.identity_revision
        visible["read"] = False
        evidence.data.update(
            revoke_evidence_id=receipt.evidence_id,
            identity_revision=receipt.identity_revision,
            effective_at=receipt.effective_at,
        )
        publish(
            case, "release", barrier_id=paused.barrier_id, revoke_evidence_id=receipt.evidence_id
        )
        # Revoked U01 cannot be the observer of its original failed job; use the legal operator.
        terminal = operator.poll_job(job_id)
        assert terminal["state"] == "failed" and terminal["error_code"] == "FORBIDDEN"
        assert terminal["result_ref"] is None
        for path in (f"/p3/operations/{job_id}/result", f"/p3/recalls/{paused.recall_id}/result"):
            assert_denied(reader.get(path), 403, "FORBIDDEN")
        try:
            final = wait_receipt(case.final_export, "job_id", job_id, FinalObservation)
        except RuntimeError:
            evidence.blocked("blocked_requirement", "Q06 final observation unavailable")
            pytest.fail("blocked_requirement: Q06 final observation unavailable")
        assert_final(final, paused, receipt, case, observer)
        assert final.job.model_dump(mode="json") == terminal
        evidence.data.update(final_evidence_id=final.evidence_id, http_checks="completed")
    except RuntimeError as exc:
        evidence.blocked("blocked_fixture", str(exc))
        raise
    finally:
        # Runs on assertion/HTTP/export timeouts; restoration advances epoch/revision.
        try:
            clean = cleanup(case, previous_revision)
            evidence.data["cleanup_evidence_id"] = clean.evidence_id
        except (RuntimeError, AssertionError, OSError) as exc:
            evidence.blocked("blocked_fixture", "cleanup/release not confirmed: " + str(exc))
            raise
