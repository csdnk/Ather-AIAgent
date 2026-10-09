"""AET-31 / RC-AUTH-13 live grant revocation before actual reranker input."""

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
from recall_prerank_revoke_support import (
    IPC_PATH_FIELDS,
    B4Observation,
    BarrierReceipt,
    GrantRevocation,
    ModelObservation,
    PrerankFinal,
    PrerankRevokeCase,
    assert_b4,
    assert_final,
    assert_model,
    assert_revoked,
    cleanup,
    publish,
)
from recall_shared_read_support import SharedRecallHarness, external_path
from recall_shared_revoke_support import wait_receipt

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
def prerank_target(request):
    evidence = SafeEvidence("RC-AUTH-13", "B4-grant-revoke")
    evidence.data.update(lane="live-http", acceptance="not_evaluated", http_checks="not_run")
    directory = (
        external_path(
            Path(
                os.environ.get(
                    "P3_RECALL_EVIDENCE_DIR",
                    str(Path(gettempdir()) / "aether-workspace-support/AET-31"),
                )
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(category, reason):
        evidence.blocked(category, reason)
        pytest.fail(category + ": " + reason)

    path = os.environ.get("P3_AUTH13_FIXTURE_FILE")
    if not path or not Path(path).is_file():
        blocked("blocked_fixture", "maintainer-prepared P3_AUTH13_FIXTURE_FILE unavailable")
    manifest = json.loads(external_path(Path(path)).read_text())
    case = PrerankRevokeCase.model_validate(manifest["case"])
    if not case.q18_capability_id or not case.b4_capability_id or not case.model_probe_id:
        blocked("blocked_fixture", "Q18/B4/actual model invocation probe unavailable")
    if not case.q06_mapping_id:
        blocked(
            "blocked_requirement", "Q06 prerank authorization/model evidence mapping unavailable"
        )
    for field in IPC_PATH_FIELDS:
        if getattr(case, field).exists():
            blocked(
                "blocked_fixture", "fresh independent fixture IPC/data paths required: " + field
            )
    url = urlsplit(manifest["base_url"])
    assert (
        url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
    )
    assert url.path in {"", "/"} and not url.query and not url.fragment
    required = {
        case.operator_env,
        *case.credential_envs.values(),
        *(m.maintainer_env for m in case.memories.values()),
    }
    if any(not os.environ.get(key) for key in required):
        blocked("blocked_fixture", "ordinary/scoped maintenance credentials unavailable")
    readers = {os.environ[key] for key in case.credential_envs.values()}
    assert len(readers) == 3 and not readers.intersection(
        {
            os.environ[case.operator_env],
            *(os.environ[m.maintainer_env] for m in case.memories.values()),
        }
    )
    subjects = {os.environ[value]: key for key, value in case.credential_envs.items()}
    allowed = {"U01": {"shared", "neighbor"}, "U08": {"u08-local"}, "U09": {"u09-local"}}

    def inspect(response):
        token = response.request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in subjects:
            return
        response.read()
        assert response.status_code != 304
        atoms = tuple(
            atom
            for key, memory in case.memories.items()
            if key not in allowed[subjects[token]]
            for atom in (memory.memory.memory_id, memory.text)
        )
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
        b4_capability_id=case.b4_capability_id,
        q18_capability_id=case.q18_capability_id,
        model_probe_id=case.model_probe_id,
        q06_mapping_id=case.q06_mapping_id,
        memories=[
            {
                "ref": m.memory.model_dump(mode="json"),
                "hash": m.body_hash,
                "body_generation": m.body_generation,
                "projection_generation": m.projection_generation,
            }
            for m in case.memories.values()
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
        yield client, case, evidence, allowed


def test_rc_auth_13_grant_revoked_after_body_read_never_reaches_model_or_pack(prerank_target):
    client, case, evidence, allowed = prerank_target
    operator = RecallHTTP(client, os.environ[case.operator_env])
    me = operator.get("/p3/auth/me")
    assert me.status_code == 200
    observer = me.json()["principal_id"]
    config = operator.get("/p3/configuration")
    assert config.status_code == 200 and config.json() == case.configuration.model_dump(mode="json")
    for name, principal in case.principals.items():
        response = RecallHTTP(client, os.environ[case.credential_envs[name]]).get("/p3/auth/me")
        assert response.status_code == 200 and response.json()["principal_id"] == name
        assert response.json()["scope"] == principal.home_scope.model_dump(mode="json")
        assert set(response.json()["permissions"]) == {p.value for p in principal.permissions}
    for memory in case.memories.values():
        maintainer = RecallHTTP(client, os.environ[memory.maintainer_env])
        response = maintainer.get(f"/p3/remember/{memory.memory.memory_id}")
        assert response.status_code == 200
        snapshot = MemorySnapshot.model_validate(response.json())
        assert snapshot.ref == memory.memory and snapshot.content == memory.text
        assert snapshot.kind == "working" and snapshot.status == "active"
        assert snapshot.projection_state == "ready" and snapshot.model_space == case.model_space
        assert snapshot.content_hash == memory.body_hash
        proof = maintainer.verify_body(memory.memory.model_dump(mode="json"), memory.text)
        assert proof["generation"] == memory.body_generation
    revision = case.initial_configuration_revision
    try:
        flow = SharedRecallHarness(client, case, observer)
        # Independently reconstruct RC-AUTH-09; no reliance on another test's grant or result.
        flow.recall_and_save("U01", "shared", "owner-control")
        flow.recall_and_save("U08", "u08-local", "recipient-before")
        flow.recall_and_save("U09", "u09-local", "no-grant-before")
        active = flow.prepare()
        revision = active.configuration_revision
        allowed["U08"].add("shared")
        saved = flow.recall_and_save("U08", "shared", "shared-model-control", active)
        control = wait_receipt(case.model_export, "job_id", saved.job_id, ModelObservation)
        assert_model(control, case, observer, saved, control=True)
        evidence.data.update(
            control_operation_id=saved.operation_id,
            control_job_id=saved.job_id,
            control_recall_id=saved.pack.recall_id,
            control_trace_id=saved.trace_id,
            control_probe_evidence_id=control.evidence_id,
            control_call_ids=[c.call_id for c in control.calls],
        )
        operation_id = "prerank-" + case.run_id
        publish(
            case,
            "arm",
            operation_id=operation_id,
            b4_capability_id=case.b4_capability_id,
            model_probe_id=case.model_probe_id,
            position="after_full_body_before_prerank_authorization",
            auto_release_seconds=120,
        )
        armed = wait_receipt(
            case.barrier_receipts, "control_operation_id", "arm-" + case.run_id, BarrierReceipt
        )
        assert armed.run_id == case.run_id and armed.operation_id == operation_id and armed.armed
        assert (
            armed.b4_capability_id == case.b4_capability_id
            and armed.model_probe_id == case.model_probe_id
        )
        recipient = RecallHTTP(client, os.environ[case.credential_envs["U08"]])
        response = client.post(
            "/p3/recall",
            json=case.request().model_dump(mode="json"),
            headers={
                "Authorization": "Bearer " + recipient.maintainer,
                "X-Operation-ID": operation_id,
            },
        )
        assert response.status_code in {202, 400}
        if response.status_code == 400:
            assert response.json()["code"] == "REQUEST_IN_PROGRESS"
        job_id = response.headers.get("X-P3-Job-ID") or response.headers.get(
            "Location", ""
        ).removeprefix("/p3/operations/")
        assert job_id and "/" not in job_id and job_id != saved.job_id
        paused = wait_receipt(case.b4_export, "job_id", job_id, B4Observation)
        assert_b4(paused, case, active, observer, operation_id, job_id)
        assert paused.recall_id != saved.pack.recall_id
        evidence.data.update(
            operation_id=operation_id,
            job_id=job_id,
            recall_id=paused.recall_id,
            trace_id=paused.trace_id,
            b4_evidence_id=paused.evidence_id,
            barrier_id=paused.barrier_id,
        )
        publish(
            case,
            "revoke",
            operation_id=operation_id,
            job_id=job_id,
            barrier_id=paused.barrier_id,
            b4_evidence_id=paused.evidence_id,
            q18_capability_id=case.q18_capability_id,
            entrypoint="Identity.provision",
            remove_grant=case.grant.model_dump(mode="json"),
            preserve_principals=True,
        )
        revoked = wait_receipt(
            case.revoke_export, "control_operation_id", "revoke-" + case.run_id, GrantRevocation
        )
        assert_revoked(revoked, paused, case, active, observer)
        revision = revoked.configuration_revision
        allowed["U08"].remove("shared")
        publish(
            case, "release", barrier_id=paused.barrier_id, revoke_evidence_id=revoked.evidence_id
        )
        terminal = recipient.poll_job(job_id)
        # Read both actual original result surfaces; do not replace a result with a new Recall.
        results = [
            recipient.get(path)
            for path in (
                f"/p3/operations/{job_id}/result",
                f"/p3/recalls/{paused.recall_id}/result",
            )
        ]
        model = wait_receipt(case.model_export, "job_id", job_id, ModelObservation)
        try:
            final = wait_receipt(case.final_export, "job_id", job_id, PrerankFinal)
        except RuntimeError:
            evidence.blocked("blocked_requirement", "Q06 prerank/result/event view unavailable")
            pytest.fail("blocked_requirement: Q06 prerank/result/event view unavailable")
        assert_final(final, model, paused, revoked, case, observer)
        assert final.job.model_dump(mode="json") == terminal
        record = recipient.get(f"/p3/recalls/{paused.recall_id}")
        assert record.status_code == 200 and record.json() == final.record.model_dump(mode="json")
        for result in results:
            if final.pack is not None:
                assert result.status_code == 200 and result.json() == final.pack.model_dump(
                    mode="json"
                )
            else:
                assert result.status_code in {403, 410}
                assert_denied(
                    result,
                    result.status_code,
                    "FORBIDDEN" if result.status_code == 403 else "RESULT_INVALIDATED",
                )
        # Grant-only revocation must not disable the caller or change its ordinary permissions.
        current = recipient.get("/p3/auth/me")
        assert current.status_code == 200
        assert set(current.json()["permissions"]) == {
            p.value for p in case.principals["U08"].permissions
        }
        evidence.data.update(
            revoke_evidence_id=revoked.evidence_id,
            effective_at=revoked.effective_at,
            model_evidence_id=model.evidence_id,
            final_evidence_id=final.evidence_id,
            observed_model_call_ids=[c.call_id for c in model.calls],
            http_checks="completed",
        )
    except RuntimeError as exc:
        evidence.blocked("blocked_fixture", str(exc))
        raise
    finally:
        try:
            receipt = cleanup(case, revision)
            evidence.data["cleanup_evidence_id"] = receipt.evidence_id
        except (RuntimeError, AssertionError, OSError) as exc:
            evidence.blocked("blocked_fixture", "B4 release/cleanup unconfirmed: " + str(exc))
            raise
