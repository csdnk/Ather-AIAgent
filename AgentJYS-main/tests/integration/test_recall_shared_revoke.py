"""AET-28 / RC-AUTH-10 live new and historical reads after grant revocation."""

import json
import os
import time
from pathlib import Path
from tempfile import gettempdir
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.recall.contracts.models import RecallRecord
from aether_agent_memory.remember.contracts.models import MemorySnapshot
from aether_agent_memory.runtime.contracts.models import TaskOperationView
from recall_authorization_support import RecallHTTP, SafeEvidence
from recall_forged_authorization_support import assert_no_index_disclosure
from recall_shared_read_support import SharedRecallHarness, SharedStageObservation, external_path
from recall_shared_revoke_support import (
    VARIANTS,
    RevokeCase,
    RevokeFinalObservation,
    assert_invalidated,
    assert_original_preserved,
    pack_hash,
    revoke_shared_grant,
    wait_receipt,
)
from recall_tenant_isolation_support import load_stage_observation

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
def revoke_target(request):
    variant = request.node.callspec.params["variant"]
    evidence = SafeEvidence("RC-AUTH-10", variant)
    evidence.data.update(lane="live-http", acceptance="not_evaluated", http_checks="not_run")
    directory = (
        external_path(
            Path(
                os.environ.get(
                    "P3_RECALL_EVIDENCE_DIR",
                    str(Path(gettempdir()) / "aether-workspace-support/AET-28"),
                )
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(reason):
        evidence.blocked("blocked_fixture", reason)
        pytest.fail("blocked_fixture: " + reason)

    path = os.environ.get("P3_AUTH10_FIXTURE_FILE")
    if not path or not Path(path).is_file():
        blocked("maintainer-prepared P3_AUTH10_FIXTURE_FILE unavailable")
    manifest = json.loads(external_path(Path(path)).read_text())
    if variant not in manifest["cases"]:
        blocked("independent revocation variant " + variant + " unavailable")
    cases = {name: RevokeCase.model_validate(value) for name, value in manifest["cases"].items()}
    case = cases[variant]
    if not case.q18_capability_id:
        blocked("Q18 controlled sharing/revocation unavailable")
    tenants = [
        c.memories[k].memory.scope.tenant_id
        for c in cases.values()
        for k in ("shared", "other-tenant")
    ]
    assert len(set(tenants)) == len(tenants), "variants must own independent scope/data"
    assert len({c.run_id for c in cases.values()}) == len(cases)
    for field in (
        "control_request",
        "control_receipts",
        "maintenance_export",
        "success_directory",
        "revoke_request",
        "revoke_export",
        "final_export",
    ):
        assert len({getattr(c, field).resolve() for c in cases.values()}) == len(cases)
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
        blocked("ordinary and legal scoped maintenance credentials unavailable")
    readers = {os.environ[key] for key in case.credential_envs.values()}
    maintainers = {
        os.environ[case.operator_env],
        *(os.environ[m.maintainer_env] for m in case.memories.values()),
    }
    assert len(readers) == 3 and not readers.intersection(maintainers)
    subjects = {os.environ[value]: key for key, value in case.credential_envs.items()}
    allowed = {"U01": {"shared", "neighbor"}, "U08": {"u08-local"}, "U09": {"u09-local"}}

    def inspect(response):
        token = response.request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token in subjects:
            response.read()
            assert response.status_code != 304, "HTTP cache cannot replay a revoked original Pack"
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
        q18_capability_id=case.q18_capability_id,
        grant_id=case.grant.grant_id,
        shared_ref=case.memories["shared"].memory.model_dump(mode="json"),
        shared_body_hash=case.memories["shared"].body_hash,
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
        yield client, case, evidence, allowed


@pytest.mark.parametrize("variant", VARIANTS)
def test_rc_auth_10_revoked_share_blocks_new_recall_and_original_result_with_stale_replicas(
    variant, revoke_target
):
    client, case, evidence, allowed = revoke_target
    operator = RecallHTTP(client, os.environ[case.operator_env])
    admin = operator.get("/p3/auth/me")
    assert admin.status_code == 200
    observer = admin.json()["principal_id"]
    config = operator.get("/p3/configuration")
    assert config.status_code == 200 and config.json() == case.configuration.model_dump(mode="json")
    identities = {}
    for name, principal in case.principals.items():
        me = RecallHTTP(client, os.environ[case.credential_envs[name]]).get("/p3/auth/me")
        assert me.status_code == 200 and me.json()["principal_id"] == name
        assert me.json()["scope"] == principal.home_scope.model_dump(mode="json")
        assert set(me.json()["permissions"]) == {p.value for p in principal.permissions}
        identities[name] = me.json()
    for memory in case.memories.values():
        maintainer = RecallHTTP(client, os.environ[memory.maintainer_env])
        response = maintainer.get(f"/p3/remember/{memory.memory.memory_id}")
        assert response.status_code == 200
        snapshot = MemorySnapshot.model_validate(response.json())
        assert snapshot.ref == memory.memory and snapshot.content == memory.text
        assert snapshot.kind == "working" and snapshot.status == "active"
        assert snapshot.projection_state == "ready" and snapshot.model_space == case.model_space
        assert snapshot.content_hash == memory.body_hash
        body = maintainer.verify_body(memory.memory.model_dump(mode="json"), memory.text)
        assert body["generation"] == memory.body_generation
    flow = SharedRecallHarness(client, case, observer)
    try:
        # Rebuild RC-AUTH-09 and F12 independently in every variant; never load another test's Pack.
        flow.recall_and_save("U01", "shared", "owner-control")
        flow.recall_and_save("U08", "u08-local", "recipient-before")
        flow.recall_and_save("U09", "u09-local", "no-grant-before")
        active = flow.prepare()
        allowed["U08"].add("shared")
        saved = flow.recall_and_save("U08", "shared", "shared-success", active)
        before = load_stage_observation(
            case.maintenance_export, saved.job_id, observation_type=SharedStageObservation
        )
        assert isinstance(before, SharedStageObservation) and before.qualification.guard is not None
        recipient = RecallHTTP(client, os.environ[case.credential_envs["U08"]])
        original_record = RecallRecord.model_validate(
            recipient.get(f"/p3/recalls/{saved.pack.recall_id}").json()
        )
        original_job = TaskOperationView.model_validate(
            recipient.get(f"/p3/operations/{saved.job_id}").json()
        )
        paths = [
            f"/p3/recalls/{saved.pack.recall_id}/result",
            f"/p3/operations/{saved.job_id}/result",
        ]
        warmed = {}
        for path in paths:
            response = recipient.get(path)
            assert response.status_code == 200 and response.json() == saved.pack.model_dump(
                mode="json"
            )
            warmed[path] = dict(response.headers)
        evidence.data.update(
            original_operation_id=saved.operation_id,
            original_job_id=saved.job_id,
            original_recall_id=saved.pack.recall_id,
            original_trace_id=saved.trace_id,
            original_pack_hash=pack_hash(saved),
            grant_evidence_id=active.evidence_id,
            original_access_event_ids=[e.event_id for e in before.events],
        )
        receipt = revoke_shared_grant(case, active, observer)
        allowed["U08"].remove("shared")  # Authority barrier, never request submission.
        evidence.data.update(
            revoke_evidence_id=receipt.evidence_id,
            submitted_at=receipt.submitted_at,
            committed_at=receipt.committed_at,
            effective_at=receipt.effective_at,
            revocation_revision=receipt.configuration_revision,
        )
        # A known legal local match prevents empty/degraded results from proving isolation.
        new_saved = flow.recall_and_save("U08", "u08-local", "after-revoke")
        evidence.data.update(
            new_operation_id=new_saved.operation_id,
            new_job_id=new_saved.job_id,
            new_recall_id=new_saved.pack.recall_id,
            new_trace_id=new_saved.trace_id,
        )
        reads = {}
        selected_paths = paths if variant == "http-cache" else [paths[VARIANTS.index(variant)]]
        for path in selected_paths:
            # O5: observe the original Record before every original result retrieval.
            record_response = recipient.get(f"/p3/recalls/{saved.pack.recall_id}")
            assert record_response.status_code == 200
            assert RecallRecord.model_validate(record_response.json()) == original_record
            headers = {"Authorization": f"Bearer {recipient.maintainer}"}
            if variant == "http-cache":
                # Use only validators actually returned by the target, never invent an ETag.
                if warmed[path].get("etag"):
                    headers["If-None-Match"] = warmed[path]["etag"]
                if warmed[path].get("last-modified"):
                    headers["If-Modified-Since"] = warmed[path]["last-modified"]
            for _ in range(2):
                response = client.get(path, headers=headers)
                assert_invalidated(response)
                request_id = response.headers.get("X-Request-ID")
                assert request_id and request_id not in reads, (
                    "original GET observation binding missing"
                )
                reads[request_id] = path
        final = wait_receipt(
            case.final_export,
            "control_operation_id",
            receipt.control_operation_id,
            RevokeFinalObservation,
        )
        assert_original_preserved(
            final,
            case,
            receipt,
            active,
            saved,
            before.events,
            original_job,
            original_record,
            before.qualification.guard,
            new_saved,
            reads,
            observer,
        )
        new_stage = load_stage_observation(
            case.maintenance_export, new_saved.job_id, observation_type=SharedStageObservation
        )
        assert final.new_events == new_stage.events
        # Only the grant changed. Ordinary identities and the owner's exact Ready body survive.
        for name in identities:
            me = RecallHTTP(client, os.environ[case.credential_envs[name]]).get("/p3/auth/me")
            assert me.status_code == 200
            for key in ("principal_id", "scope", "permissions"):
                assert me.json()[key] == identities[name][key]
        memory = case.memories["shared"]
        owner = RecallHTTP(client, os.environ[case.credential_envs["U01"]])
        owner.verify_body(memory.memory.model_dump(mode="json"), memory.text)
        evidence.data.update(final_evidence_id=final.evidence_id, http_checks="completed")
    except RuntimeError as exc:
        evidence.blocked("blocked_fixture", str(exc))
        raise
