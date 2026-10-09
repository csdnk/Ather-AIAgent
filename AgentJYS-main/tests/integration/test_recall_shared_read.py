"""AET-27 / RC-AUTH-09 live explicit READ sharing with external Q18 authority."""

import json
import os
import time
from pathlib import Path
from tempfile import gettempdir
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.remember.contracts.foundation import FullBodyReadResult
from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    DeleteRequest,
    MemorySnapshot,
    RememberRequest,
)
from recall_authorization_support import RecallHTTP, SafeEvidence, assert_denied
from recall_forged_authorization_support import assert_no_index_disclosure
from recall_shared_read_support import VARIANTS, SharedReadCase, SharedRecallHarness

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
def shared_target(request):
    variant = request.node.callspec.params["variant"]
    evidence = SafeEvidence("RC-AUTH-09", variant)
    evidence.data.update(acceptance="not_evaluated", http_checks="not_run")
    directory = (
        Path(
            os.environ.get(
                "P3_RECALL_EVIDENCE_DIR",
                str(Path(gettempdir()) / "aether-workspace-support/AET-27"),
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(reason):
        evidence.blocked("blocked_fixture", reason)
        pytest.fail("blocked_fixture: " + reason)

    path = os.environ.get("P3_AUTH09_FIXTURE_FILE")
    if not path or not Path(path).is_file():
        blocked("maintainer-prepared P3_AUTH09_FIXTURE_FILE unavailable")
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if variant not in manifest["cases"]:
        blocked("independent sharing variant " + variant + " unavailable")
    cases = {
        name: SharedReadCase.model_validate(value) for name, value in manifest["cases"].items()
    }
    case = cases[variant]
    if not case.q18_capability_id:
        blocked("Q18 controlled grant preparation unavailable")
    tenants = [
        c.memories[key].memory.scope.tenant_id
        for c in cases.values()
        for key in ("shared", "other-tenant")
    ]
    assert len(set(tenants)) == 2 * len(cases), "independent variant scope/data required"
    assert len({c.run_id for c in cases.values()}) == len(cases)
    for field in ("control_request", "control_receipts", "success_directory", "maintenance_export"):
        paths = [getattr(c, field).resolve() for c in cases.values()]
        assert len(set(paths)) == len(paths), (
            "variant controller/evidence paths must be independent"
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
    if any(not os.environ.get(name) for name in required):
        blocked(
            "owner/recipient/no-grant/scoped maintainer/platform operator credentials unavailable"
        )
    readers = {os.environ[name] for name in case.credential_envs.values()}
    maintainers = {
        os.environ[case.operator_env],
        *(os.environ[m.maintainer_env] for m in case.memories.values()),
    }
    assert len(readers) == 3 and not readers.intersection(maintainers)
    allowed = {"U01": {"shared", "neighbor"}, "U08": {"u08-local"}, "U09": {"u09-local"}}
    token_subjects = {os.environ[env]: name for name, env in case.credential_envs.items()}

    def inspect(response):
        token = response.request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token in token_subjects:
            response.read()
            principal = token_subjects[token]
            visible = allowed[principal]
            private = tuple(
                atom
                for key, m in case.memories.items()
                if key not in visible
                for atom in (m.memory.memory_id, m.text)
            )
            assert_no_index_disclosure(response.json(), private)
            assert_no_index_disclosure(dict(response.headers), private)
            evidence.response(response.request.method, response.request.url.path, response)

    evidence.data.update(
        lane="live-http-deployment-managed-grant",
        run_id=case.run_id,
        source_sha=case.source_sha,
        image_digest=case.image_digest,
        config_hash=case.configuration.config_hash,
        backend_binding=case.backend_binding,
        model_binding=case.model_binding,
        q18_capability_id=case.q18_capability_id,
        grant_id=case.grant.grant_id,
        memories=[
            {
                "key": key,
                "ref": m.memory.model_dump(mode="json"),
                "body_hash": m.body_hash,
                "body_generation": m.body_generation,
                "projection_generation": m.projection_generation,
            }
            for key, m in case.memories.items()
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
        yield client, case, evidence, allowed


@pytest.mark.parametrize("variant", VARIANTS)
def test_rc_auth_09_explicit_read_grant_delivers_only_the_exact_current_memory(
    variant, shared_target
):
    client, case, evidence, allowed = shared_target
    operator = RecallHTTP(client, os.environ[case.operator_env])
    admin_me = operator.get("/p3/auth/me")
    assert admin_me.status_code == 200
    config = operator.get("/p3/configuration")
    assert config.status_code == 200 and config.json() == case.configuration.model_dump(mode="json")
    identities = {}
    for name, principal in case.principals.items():
        harness = RecallHTTP(client, os.environ[case.credential_envs[name]])
        me = harness.get("/p3/auth/me")
        assert me.status_code == 200 and me.json()["principal_id"] == name
        assert me.json()["scope"] == principal.home_scope.model_dump(mode="json")
        assert set(me.json()["permissions"]) == {p.value for p in principal.permissions}
        identities[name] = me.json()

    def verify_fixture(key):
        memory = case.memories[key]
        harness = RecallHTTP(client, os.environ[memory.maintainer_env])
        response = harness.get(f"/p3/remember/{memory.memory.memory_id}")
        assert response.status_code == 200
        snapshot = MemorySnapshot.model_validate(response.json())
        assert snapshot.ref == memory.memory and snapshot.content == memory.text
        assert (
            snapshot.kind == "working"
            and snapshot.status == "active"
            and snapshot.projection_state == "ready"
        )
        assert (
            snapshot.content_hash == memory.body_hash and snapshot.model_space == case.model_space
        )
        body_response = client.post(
            "/p3/remember/body",
            json=memory.memory.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {harness.maintainer}"},
        )
        assert body_response.status_code == 200
        body = FullBodyReadResult.model_validate(body_response.json())
        assert (
            body.outcome == "read" and body.memory == memory.memory and body.content == memory.text
        )
        assert (
            body.sources == memory.sources and body.guard is not None and body.location is not None
        )
        assert body.guard.body_hash == body.location.content_hash == memory.body_hash
        assert body.location.generation == memory.body_generation

    for key in case.memories:
        verify_fixture(key)
    flow = SharedRecallHarness(client, case, admin_me.json()["principal_id"])

    def recall(name, key, label, receipt=None):
        try:
            saved = flow.recall_and_save(name, key, label, receipt)
        except RuntimeError as exc:
            evidence.blocked("blocked_fixture", str(exc))
            raise
        evidence.data.setdefault("operations", []).append(
            {
                "principal_id": name,
                "operation_id": saved.operation_id,
                "job_id": saved.job_id,
                "recall_id": saved.pack.recall_id,
                "trace_id": saved.trace_id,
                "grant_evidence_id": saved.grant_evidence_id,
            }
        )
        return saved

    recall("U01", "shared", "owner-control")
    recall("U08", "u08-local", "recipient-before")
    recall("U09", "u09-local", "no-grant-before")
    if variant == "remember-preparation":
        evidence.blocked(
            "blocked_fixture",
            "Remember sharing entrypoint unavailable; current provider is Identity.provision",
        )
        pytest.fail(
            "blocked_fixture: no Remember sharing entrypoint; "
            "deployment grant path is tested separately"
        )
    try:
        receipt = flow.prepare()
    except RuntimeError as exc:
        evidence.blocked("blocked_fixture", str(exc))
        raise
    allowed["U08"].add("shared")  # Only after the actual authority receipt, never on intent.
    evidence.data.update(
        grant_evidence_id=receipt.evidence_id,
        grant_revision=case.grant.revision,
        controller_entrypoint=receipt.entrypoint,
        configuration_revision=receipt.configuration_revision,
    )
    shared = recall("U08", "shared", "shared-success", receipt)
    recall("U09", "u09-local", "no-grant-after")
    recipient = RecallHTTP(client, os.environ[case.credential_envs["U08"]])
    control = RecallHTTP(client, os.environ[case.credential_envs["U09"]])
    target = case.memories["shared"]
    metadata = recipient.get(f"/p3/remember/{target.memory.memory_id}")
    assert metadata.status_code == 200
    assert MemorySnapshot.model_validate(metadata.json()).ref == target.memory
    assert_denied(control.get(f"/p3/remember/{target.memory.memory_id}"), 403, "FORBIDDEN")
    for key in ("neighbor", "other-scope", "other-tenant"):
        assert_denied(
            recipient.get(f"/p3/remember/{case.memories[key].memory.memory_id}"), 403, "FORBIDDEN"
        )

    if variant in {"read-not-write", "read-not-delete"}:
        assert receipt.qualification.guard is not None
        if variant == "read-not-write":
            source = {
                "kind": "text",
                "external_id": "denied-" + case.run_id,
                "external_version": "1",
                "occurred_at": receipt.observed_at,
            }
            correction = CorrectionRequest.model_validate(
                {
                    "expected_version": target.memory.version,
                    "expected_object_revision": receipt.qualification.guard.object_revision,
                    "content": "Unauthorized replacement " + case.run_id,
                    "source": source,
                    "reason": "READ grant isolation test",
                }
            )
            create = RememberRequest.model_validate(
                {
                    "source": source,
                    "selection": {
                        k: v
                        for k, v in target.memory.scope.model_dump(exclude_none=True).items()
                        if k != "tenant_id"
                    },
                    "content": {
                        "kind": "text",
                        "text": "Unauthorized owner-scope write " + case.run_id,
                    },
                }
            )
            commands = [
                (
                    f"/p3/remember/{target.memory.memory_id}/correct",
                    correction.model_dump(mode="json"),
                ),
                ("/p3/remember", create.model_dump(mode="json")),
            ]
        else:
            deletion = DeleteRequest(
                expected_revision=receipt.qualification.guard.object_revision,
                reason="READ grant isolation test",
            )
            commands = [
                (f"/p3/remember/{target.memory.memory_id}/delete", deletion.model_dump(mode="json"))
            ]
        for index, (path, payload) in enumerate(commands):
            response = client.post(
                path,
                json=payload,
                headers={
                    "Authorization": f"Bearer {recipient.maintainer}",
                    "X-Operation-ID": f"denied-{case.run_id}-{index}",
                },
            )
            if response.status_code in {404, 405}:
                evidence.blocked(
                    "blocked_fixture",
                    "actual Remember mutation route unavailable: " + path.rsplit("/", 1)[-1],
                )
                pytest.fail("blocked_fixture: actual Remember mutation route unavailable")
            assert_denied(response, 403, "FORBIDDEN")
            assert not response.headers.get("X-P3-Job-ID") and not response.headers.get("Location")
    verify_fixture("shared")  # Owner scope, exact version and body remain unchanged.
    for name in ("U08", "U09"):
        harness = RecallHTTP(client, os.environ[case.credential_envs[name]])
        me = harness.get("/p3/auth/me")
        assert me.status_code == 200
        for field in ("principal_id", "scope", "permissions"):
            assert me.json()[field] == identities[name][field]
    flow.verify_original("U08", shared)
    evidence.data["http_checks"] = "completed"
