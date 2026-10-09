"""AET-32 / RC-AUTH-14 real GET matrix; unresolved policy cells stay blocked."""

import json
import os
import time
from pathlib import Path
from tempfile import gettempdir
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.recall.contracts.models import ContextPack
from aether_agent_memory.remember.contracts.models import MemorySnapshot
from aether_agent_memory.runtime.contracts.models import Permission
from recall_authorization_support import RecallHTTP, SafeEvidence
from recall_ownership_matrix_support import (
    VARIANTS,
    OwnershipCase,
    OwnershipStage,
    assert_no_secrets,
    assert_rule,
    assert_safety,
    assert_visible,
    endpoint,
    read_policy,
)
from recall_shared_read_support import SavedSharedPack, external_path
from recall_shared_revoke_support import pack_hash
from recall_tenant_isolation_support import F4Actor, assert_stage_isolation, load_stage_observation

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
def ownership_target(request):
    variant = request.node.callspec.params["variant"]
    evidence = SafeEvidence("RC-AUTH-14", variant)
    evidence.data.update(lane="live-http", acceptance="not_evaluated", safety_checks="not_run")
    directory = (
        external_path(
            Path(
                os.environ.get(
                    "P3_RECALL_EVIDENCE_DIR",
                    str(Path(gettempdir()) / "aether-workspace-support/AET-32"),
                )
            )
        )
        / uuid4().hex
    )
    request.addfinalizer(lambda: evidence.write(directory))

    def blocked(reason):
        evidence.blocked("blocked_fixture", reason)
        pytest.fail("blocked_fixture: " + reason)

    path = os.environ.get("P3_AUTH14_FIXTURE_FILE")
    if not path or not Path(path).is_file():
        blocked("maintainer-prepared P3_AUTH14_FIXTURE_FILE unavailable")
    manifest = json.loads(external_path(Path(path)).read_text())
    if variant not in manifest["cases"]:
        blocked("independent matrix cell unavailable: " + variant)
    cases = {key: OwnershipCase.model_validate(raw) for key, raw in manifest["cases"].items()}
    case = cases[variant]
    assert len({c.run_id for c in cases.values()}) == len(cases)
    assert len({c.principals["U01"].home_scope.tenant_id for c in cases.values()}) == len(cases)
    for field in ("maintenance_export", "success_directory", "policy_file"):
        assert len({getattr(c, field).resolve() for c in cases.values()}) == len(cases)
    if variant.startswith("U02-"):
        assert case.foreign_dimension == (
            "user_id" if variant.startswith("U02-user") else "agent_id"
        )
    if case.maintenance_export.exists() or case.success_directory.exists():
        blocked("fresh independent export/Pack directory required for each matrix execution")
    required = {
        case.operator_env,
        *case.credential_envs.values(),
        *(m.maintainer_env for m in case.memories),
    }
    if any(not os.environ.get(key) for key in required):
        blocked("ordinary and legal scoped maintenance credentials unavailable")
    ordinary = {os.environ[key] for key in case.credential_envs.values()}
    maintenance = {
        os.environ[case.operator_env],
        *(os.environ[m.maintainer_env] for m in case.memories),
    }
    assert len(ordinary) == 4 and not ordinary.intersection(maintenance)
    url = urlsplit(manifest["base_url"])
    assert (
        url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
    )
    assert url.path in {"", "/"} and not url.query and not url.fragment
    secrets = tuple(ordinary | maintenance)
    evidence.data.update(
        run_id=case.run_id,
        source_sha=case.source_sha,
        image_digest=case.image_digest,
        config_hash=case.configuration.config_hash,
        backend_binding=case.backend_binding,
        model_binding=case.model_binding,
        permission_enum={"READ": Permission.READ.value, "DIAGNOSE": Permission.DIAGNOSE.value},
        memories=[
            {
                "ref": m.memory.model_dump(mode="json"),
                "body_hash": m.body_hash,
                "body_generation": m.body_generation,
                "projection_generation": m.projection_generation,
            }
            for m in case.memories
        ],
    )
    with httpx.Client(base_url=manifest["base_url"], timeout=30, follow_redirects=False) as client:
        deadline = time.monotonic() + 60
        while client.get("/p3/readyz").status_code != 200:
            if time.monotonic() >= deadline:
                blocked("Ready barrier unavailable")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield client, case, evidence, secrets


def original_success(client, case, evidence, observer, secrets):
    token = os.environ[case.credential_envs["U01"]]
    owner = RecallHTTP(client, token)
    operation_id = "ownership-" + case.run_id
    payload, job_id = owner.command(
        "/p3/recall",
        case.request.model_dump(mode="json"),
        operation_id,
        token,
    )
    pack = ContextPack.model_validate(payload)
    coffee = case.memories[0]
    assert pack.scope == case.principals["U01"].home_scope and pack.outcome == "available"
    items = [item for group in pack.groups for item in group.items]
    assert len(items) == 1 and items[0].memory == coffee.memory
    assert items[0].content == coffee.text and items[0].sources == coffee.sources
    assert coffee.text in pack.rendered_context
    operator = RecallHTTP(client, os.environ[case.operator_env])
    admin = operator.get(f"/p3/admin/tasks/{job_id}")
    if admin.status_code != 200:
        raise RuntimeError("blocked_fixture: legal original task/trace evidence unavailable")
    assert admin.json()["task"]["task_id"] == job_id
    trace = admin.json()["traces"]["trace_id"]
    stage = load_stage_observation(case.maintenance_export, job_id, observation_type=OwnershipStage)
    assert isinstance(stage, OwnershipStage)
    actor = F4Actor.model_validate(
        {
            **coffee.model_dump(mode="json"),
            "name": "U01",
            "credential_env": case.credential_envs["U01"],
        }
    )
    assert_stage_isolation(
        stage, case, actor, operation_id, job_id, pack.recall_id, trace, observer
    )
    assert stage.principal == case.principals["U01"] and not stage.consumed_grant_ids
    assert not set(case.principals).intersection(stage.maintenance_principal_ids), (
        "ordinary DIAGNOSE callers must not be configured as deployment administrators"
    )
    saved = SavedSharedPack(
        operation_id=operation_id, job_id=job_id, trace_id=trace, grant_evidence_id=None, pack=pack
    )
    evidence.data.update(
        operation_id=operation_id,
        job_id=job_id,
        recall_id=pack.recall_id,
        trace_id=trace,
        pack_hash=pack_hash(saved),
        stage_evidence_id=stage.evidence_id,
    )
    directory = external_path(case.success_directory)
    directory.mkdir(parents=True, exist_ok=True)
    with os.fdopen(
        os.open(directory / "F12.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as stream:
        stream.write(saved.model_dump_json())
    for path in (f"/p3/operations/{job_id}/result", f"/p3/recalls/{pack.recall_id}/result"):
        response = owner.get(path)
        assert_safety(response, case, saved, "U01", "recall-result", secrets)
        assert response.status_code == 200 and response.json() == payload
    # A successful readyz cannot establish that original diagnostics or Trace actually exist.
    for surface in ("diagnostic-task", "trace-detail"):
        deadline = time.monotonic() + 30
        while True:
            diagnostic = owner.get(endpoint(surface, saved))
            assert_safety(diagnostic, case, saved, "U01", surface, secrets)
            if diagnostic.status_code != 200:
                raise RuntimeError("blocked_fixture: legal original diagnostic control unavailable")
            assert_visible(
                diagnostic.json(), surface, saved, case, "U01", require_trace_records=False
            )
            if surface != "trace-detail" or diagnostic.json()["records"]:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("blocked_fixture: retained original Trace control unavailable")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    return saved


@pytest.mark.parametrize("variant", VARIANTS)
def test_rc_auth_14_diagnose_and_read_respect_scope_and_original_trace_ownership(
    variant,
    ownership_target,
):
    client, case, evidence, secrets = ownership_target
    viewer, surface = variant.split(":")
    principal_name = viewer.split("-")[0]
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
        assert_no_secrets(response.json(), secrets)
    for memory in case.memories:
        maintainer = RecallHTTP(client, os.environ[memory.maintainer_env])
        response = maintainer.get(f"/p3/remember/{memory.memory.memory_id}")
        assert response.status_code == 200
        snapshot = MemorySnapshot.model_validate(response.json())
        assert snapshot.ref == memory.memory and snapshot.content == memory.text
        assert snapshot.kind == "working" and snapshot.status == "active"
        assert snapshot.projection_state == "ready" and snapshot.content_hash == memory.body_hash
        assert snapshot.model_space == case.model_space
        body = maintainer.verify_body(memory.memory.model_dump(mode="json"), memory.text)
        assert body["generation"] == memory.body_generation
    try:
        saved = original_success(client, case, evidence, observer, secrets)
        policy, rule = read_policy(case, variant)
        if policy:
            evidence.data.update(
                q01_evidence_id=policy.q01_evidence_id,
                q06_evidence_id=policy.q06_evidence_id,
                policy_sha256=policy.policy_sha256,
            )
        reader = RecallHTTP(client, os.environ[case.credential_envs[principal_name]])
        path = endpoint(surface, saved)
        cursor = None
        seen = set()
        pages = []
        deadline = time.monotonic() + 30
        # Traverse all current pages to catch leakage hidden behind the first filtered page.
        while True:
            params = {"limit": 100}
            if surface == "trace-list":
                params["flow"] = "recall"
                if cursor is not None:
                    params["before"] = cursor
            elif surface == "trace-detail" and cursor is not None:
                params["after"] = cursor
            response = reader.get(
                path, **({"params": params} if surface.startswith("trace-") else {})
            )
            assert_safety(response, case, saved, viewer, surface, secrets)
            evidence.response("GET", path, response)
            pages.append(response)
            if rule:
                assert_rule(response, rule, surface, saved, case, viewer)
            elif response.status_code == 200:
                # Unresolved status does not suspend field/body/ownership assertions.
                assert_visible(response.json(), surface, saved, case, viewer)
            if response.status_code != 200 or not surface.startswith("trace-"):
                break
            cursor = response.json().get("next_before" if surface == "trace-list" else "next_after")
            if cursor is None:
                break
            assert cursor not in seen, "Trace pagination did not advance"
            seen.add(cursor)
            assert time.monotonic() < deadline, "Trace pagination exceeded bounded wait"
        evidence.data.update(safety_checks="completed", observed_pages=len(pages))
        if rule and viewer == "U01" and surface == "trace-list" and rule.decision == "visible":
            assert any(
                row["trace_id"] == saved.trace_id and row["record_count"] > 0
                for page in pages
                for row in page.json()["items"]
            ), "original Trace missing"
        if rule is None:
            evidence.blocked(
                "blocked_requirement",
                "Q01/Q06 visibility/HTTP/field mapping unresolved: " + variant,
            )
            pytest.fail("blocked_requirement: unresolved Q01/Q06 matrix cell " + variant)
    except RuntimeError as exc:
        evidence.blocked("blocked_fixture", str(exc))
        raise
