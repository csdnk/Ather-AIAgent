"""AET-23 / RC-AUTH-03: live HTTP cases using maintained same-ID F4 data.

Implementation is independent of local backend availability. No production
auth override, memory-ID write API, client K field, or unconditional XFAIL.
"""

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
from recall_tenant_isolation_support import (
    F4Case,
    assert_isolated_pack,
    assert_public_isolation,
    assert_stage_isolation,
    assert_top1_pressure,
    load_stage_observation,
)

pytestmark = [pytest.mark.integration, pytest.mark.p0]
VARIANTS = (
    "a-first-a-lower",
    "b-first-a-lower",
    "a-first-b-lower",
    "b-first-b-lower",
    "a-first-same-body-cache",
    "b-first-same-body-cache",
)


@pytest.fixture
def f4_target(request):
    variant = request.node.callspec.params["variant"]
    evidence = SafeEvidence("RC-AUTH-03", variant)
    evidence.data.update(http_checks="not_run", acceptance="not_evaluated")
    directory = (
        Path(
            os.environ.get(
                "P3_RECALL_EVIDENCE_DIR",
                str(Path(gettempdir()) / "aether-workspace-support/AET-23"),
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))
    manifest_path = os.environ.get("P3_AUTH03_FIXTURE_FILE")
    if not manifest_path or not Path(manifest_path).is_file():
        evidence.blocked("blocked_fixture", "maintainer-prepared F4 P3_AUTH03_FIXTURE_FILE")
        pytest.fail("blocked_fixture: maintainer-prepared same-ID F4 manifest unavailable")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if variant not in manifest["cases"]:
        evidence.blocked("blocked_fixture", "independent F4 variant " + variant)
        pytest.fail("blocked_fixture: independent F4 variant unavailable")
    cases = {name: F4Case.model_validate(value) for name, value in manifest["cases"].items()}
    case = cases[variant]
    all_tenants = [
        actor.memory.scope.tenant_id for value in cases.values() for actor in value.actors
    ]
    assert len(set(all_tenants)) == len(all_tenants), "variants must own independent tenant data"
    assert len({value.run_id for value in cases.values()}) == len(cases), (
        "duplicate F4 run identity"
    )
    url = urlsplit(manifest["base_url"])
    assert (
        url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
    )
    assert not url.query and not url.fragment, "target must be a configured API origin"
    required = {
        name for actor in case.actors for name in (actor.credential_env, actor.maintainer_env)
    } | {case.operator_env}
    missing = sorted(name for name in required if not os.environ.get(name))
    for name in missing:
        evidence.blocked("blocked_fixture", name)
    if missing:
        pytest.fail("blocked_fixture: missing F4 credential environment references")
    readers = {os.environ[actor.credential_env] for actor in case.actors}
    maintainers = {os.environ[actor.maintainer_env] for actor in case.actors} | {
        os.environ[case.operator_env]
    }
    assert len(readers) == 2 and not readers.intersection(maintainers), (
        "separate reader/maintenance credentials required"
    )
    if "same-body-cache" in variant:
        assert case.actors[0].text == case.actors[1].text, (
            "cache variant requires equal body hashes"
        )
    else:
        assert case.actors[0].text != case.actors[1].text, (
            "pressure variants require distinct canaries"
        )
    evidence.data.update(
        lane="live-http-maintained-F4",
        run_id=case.run_id,
        source_sha=case.source_sha,
        image_digest=case.image_digest,
        config_hash=case.configuration.config_hash,
        server_candidate_limit=case.server_settings.candidate_limit,
        backend_binding=case.backend_binding,
        model_binding=case.model_binding,
        memories=[
            {
                "ref": actor.memory.model_dump(mode="json"),
                "body_hash": actor.body_hash,
                "body_generation": actor.body_generation,
                "projection_generation": actor.projection_generation,
            }
            for actor in case.actors
        ],
    )
    forbidden = {}
    for actor in case.actors:
        other = next(value for value in case.actors if value.name != actor.name)
        atoms = [other.memory.scope.tenant_id]
        if actor.text != other.text:
            atoms.append(other.text)
        own_sources = {source.source_id for source in actor.sources}
        atoms.extend(
            source.source_id for source in other.sources if source.source_id not in own_sources
        )
        forbidden[os.environ[actor.credential_env]] = atoms

    def inspect_ordinary_response(response):
        credential = response.request.headers.get("Authorization", "").removeprefix("Bearer ")
        if credential in forbidden:
            response.read()
            assert_public_isolation(response.json(), tuple(forbidden[credential]))
            if response.status_code >= 400:
                error = ErrorResponse.model_validate(response.json())
                assert error.message == error.code.value.lower(), "free-form error content leak"
            evidence.response(response.request.method, response.request.url.path, response)

    with httpx.Client(
        base_url=manifest["base_url"],
        timeout=30,
        follow_redirects=False,
        event_hooks={"response": [inspect_ordinary_response]},
    ) as client:
        deadline = time.monotonic() + 60
        while client.get("/p3/readyz").status_code != 200:
            if time.monotonic() >= deadline:
                evidence.blocked("blocked_fixture", "target Ready barrier")
                pytest.fail("blocked_fixture: target never reached Ready")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield client, case, evidence, forbidden


@pytest.mark.parametrize("variant", VARIANTS)
def test_rc_auth_03_same_ids_top1_and_original_results_stay_tenant_scoped(variant, f4_target):
    client, case, evidence, forbidden = f4_target
    actors = {actor.name: actor for actor in case.actors}
    order = ("U01", "U04") if variant.startswith("a-first") else ("U04", "U01")
    operation_id = "f4-" + case.run_id  # Exactly the same ID and body in both tenants.
    payload = case.request.model_dump(mode="json")
    assert "candidate_limit" not in payload and "k" not in payload and "top_k" not in payload
    controls, sources, observers = {}, {}, {}
    operator = RecallHTTP(client, os.environ[case.operator_env])
    operator_me = operator.get("/p3/auth/me")
    assert operator_me.status_code == 200
    active = operator.get("/p3/configuration")
    assert active.status_code == 200 and active.json() == case.configuration.model_dump(mode="json")
    for name in order:
        actor = actors[name]
        token, maintainer = os.environ[actor.credential_env], os.environ[actor.maintainer_env]
        harness = RecallHTTP(client, maintainer)
        me = harness.get("/p3/auth/me", token)
        assert me.status_code == 200 and me.json()["principal_id"] == name
        assert Permission.READ.value in me.json()["permissions"]
        for field in ("tenant_id", "application_id", "user_id", "agent_id"):
            assert me.json()["scope"][field] == getattr(actor.memory.scope, field)
        maint = harness.get("/p3/auth/me")
        assert maint.status_code == 200
        assert Permission.READ.value in maint.json()["permissions"]
        observers[name] = operator_me.json()["principal_id"]
        snapshot = harness.get(f"/p3/remember/{actor.memory.memory_id}")
        assert snapshot.status_code == 200
        memory = MemorySnapshot.model_validate(snapshot.json())
        assert memory.ref == actor.memory and memory.kind == "working"
        assert memory.projection_state == "ready" and memory.status == "active"
        assert memory.model_space == case.model_space
        assert memory.content_hash == actor.body_hash and memory.content == actor.text
        body = client.post(
            "/p3/remember/body",
            json=actor.memory.model_dump(mode="json"),
            headers={
                "Authorization": f"Bearer {maintainer}",
            },
        )
        assert body.status_code == 200
        complete = FullBodyReadResult.model_validate(body.json())
        assert complete.outcome == "read" and complete.memory == actor.memory
        assert complete.sources == actor.sources, "F4 sources differ from the prepared fixture"
        assert complete.guard is not None and complete.location is not None
        assert complete.content == actor.text and complete.guard.body_hash == actor.body_hash
        assert complete.location.generation == actor.body_generation
        sources[name] = complete.sources
        result, job_id = harness.command("/p3/recall", payload, operation_id, token)
        other = actors["U04" if name == "U01" else "U01"]
        foreign_bodies = (other.text,) if actor.text != other.text else ()
        pack = assert_isolated_pack(
            result, actor.memory.model_dump(mode="json"), actor.text, foreign_bodies
        )
        assert tuple(item.sources for group in pack.groups for item in group.items) == (
            sources[name],
        )
        controls[name] = (result, pack, job_id)
    assert controls["U01"][2] != controls["U04"][2], "same operation ID collided across tenants"
    assert controls["U01"][1].recall_id != controls["U04"][1].recall_id
    for name, actor in actors.items():
        other_name = "U04" if name == "U01" else "U01"
        forbidden[os.environ[actor.credential_env]].extend(
            [controls[other_name][2], controls[other_name][1].recall_id]
        )
    # A foreign-only operation must not become discoverable in the other tenant.
    unique_operations = {}
    for name in order:
        actor = actors[name]
        harness = RecallHTTP(client, os.environ[actor.maintainer_env])
        unique_operation = operation_id + "-" + name
        unique_result, unique_job = harness.command(
            "/p3/recall", payload, unique_operation, os.environ[actor.credential_env]
        )
        other = actors["U04" if name == "U01" else "U01"]
        unique_pack = assert_isolated_pack(
            unique_result,
            actor.memory.model_dump(mode="json"),
            actor.text,
            (other.text,) if actor.text != other.text else (),
        )
        unique_operations[name] = unique_operation
        forbidden[os.environ[other.credential_env]].extend([unique_job, unique_pack.recall_id])
    for name in order:
        actor = actors[name]
        other_name = "U04" if name == "U01" else "U01"
        harness = RecallHTTP(client, os.environ[actor.maintainer_env])
        foreign_lookup = harness.get(
            f"/p3/operation-requests/{unique_operations[other_name]}",
            os.environ[actor.credential_env],
            params={"kind": "recall.execute"},
        )
        assert foreign_lookup.status_code == 200
        hidden = OperationLookup.model_validate(foreign_lookup.json())
        assert hidden.state == "unconfirmed" and hidden.job_id is None
        assert (
            hidden.input_hash is None and hidden.workflow_id is None and hidden.http_request is None
        )
    event_ids = {}
    # Interleave repeated GETs and known-success replay to expose warm cache/dedup collisions.
    for name in (*order, *reversed(order)):
        actor = actors[name]
        other_name = "U04" if name == "U01" else "U01"
        other = actors[other_name]
        result, pack, job_id = controls[name]
        token = os.environ[actor.credential_env]
        harness = RecallHTTP(client, os.environ[actor.maintainer_env])
        lookup = harness.get(
            f"/p3/operation-requests/{operation_id}", token, params={"kind": "recall.execute"}
        )
        assert lookup.status_code == 200
        original = OperationLookup.model_validate(lookup.json())
        assert original.state == "found" and original.job_id == job_id
        assert original.task_state == "succeeded" and original.operation_id == operation_id
        for path in (f"/p3/recalls/{pack.recall_id}/result", f"/p3/operations/{job_id}/result"):
            reply = harness.get(path, token)
            assert reply.status_code == 200 and reply.json() == result, "original Pack changed"
            assert other.memory.scope.tenant_id not in json.dumps(reply.json(), ensure_ascii=False)
            evidence.response("GET", path, reply)
        record = harness.get(f"/p3/recalls/{pack.recall_id}", token)
        assert record.status_code == 200
        state = RecallRecord.model_validate(record.json())
        assert (
            state.scope == actor.memory.scope
            and state.state == "completed"
            and state.result_available
        )
        job = harness.get(f"/p3/operations/{job_id}", token)
        assert job.status_code == 200
        task = TaskOperationView.model_validate(job.json())
        assert task.initiator_id == name and task.subject.scope == actor.memory.scope
        assert task.result_ref is not None
        assert task.result_ref.scope == actor.memory.scope
        for path in (
            f"/p3/recalls/{controls[other_name][1].recall_id}",
            f"/p3/recalls/{controls[other_name][1].recall_id}/result",
            f"/p3/operations/{controls[other_name][2]}",
            f"/p3/operations/{controls[other_name][2]}/result",
        ):
            denied = harness.get(path, token)
            assert_denied(denied, 403, "FORBIDDEN")  # Bound to current P3 HTTP/Identity source.
            evidence.response("GET", path, denied)
        replay, replay_job = harness.command("/p3/recall", payload, operation_id, token)
        assert replay_job == job_id and replay == result, "cross-tenant dedup/replay collision"
        # Only authorized admin views handle private stage/count/foreign candidate evidence.
        admin = operator.get(f"/p3/admin/tasks/{job_id}")
        assert admin.status_code == 200, "blocked_fixture: legal maintenance task view unavailable"
        assert admin.json()["task"]["task_id"] == job_id
        trace_id = admin.json()["traces"]["trace_id"]
        try:
            stage = load_stage_observation(case.maintenance_export, job_id)
        except RuntimeError:
            evidence.blocked("blocked_fixture", "authorized stage receipt for original job")
            raise
        event_ids[name] = assert_stage_isolation(
            stage, case, actor, operation_id, job_id, pack.recall_id, trace_id, observers[name]
        )
        if "same-body-cache" in variant:
            assert stage.body_paths == ("cache",), "warm-cache variant must exercise a real hit"
        elif name == ("U01" if variant.endswith("a-lower") else "U04"):
            assert_top1_pressure(
                [hit.model_dump(mode="json") for hit in stage.unfiltered_probe],
                actor.memory.model_dump(mode="json"),
                other.memory.model_dump(mode="json"),
            )
            for hit in stage.unfiltered_probe:
                owner = next(a for a in case.actors if a.memory == hit.target.memory)
                assert hit.target.body_hash == owner.body_hash
                assert hit.target.generation == owner.projection_generation
                assert hit.target.model_space == case.model_space
                assert hit.target.memory_source == "working"
        evidence.data.setdefault("operations", {})[name] = {
            "operation_id": operation_id,
            "job_id": job_id,
            "recall_id": pack.recall_id,
            "trace_id": trace_id,
            "evidence_id": stage.evidence_id,
            "pack_hash": sha256(pack.model_dump_json().encode()).hexdigest(),
        }
    assert not event_ids["U01"].intersection(event_ids["U04"]), "cross-tenant event dedup collision"
    evidence.data["http_checks"] = "completed"
