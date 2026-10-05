"""Security regressions for native Keycloak organization snapshots."""

from hashlib import sha256

import httpx
import pytest
from pydantic import ValidationError

from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, Principal, Scope
from aether_agent_memory.runtime.flows.config import JWTIssuerConfiguration
from aether_agent_memory.runtime.flows.keycloak_directory import (
    DirectoryUnavailableError,
    KeycloakReader,
    store_snapshot,
)
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.identity import jwt_issuer_policy_hash
from azure_test_runtime import Foundation


@pytest.fixture
def issuer(tmp_path):
    secret = tmp_path / "directory-secret"
    secret.write_text("test-secret", encoding="utf-8")
    return JWTIssuerConfiguration.model_validate(
        {
            "issuer": "https://identity.example/realms/p3",
            "jwks_url": "https://identity.example/realms/p3/keys",
            "audience": "p3",
            "directory": {
                "client_id": "directory",
                "client_secret_file": str(secret),
                "roles_client_id": "p3",
                "organizations": [
                    {"organization_id": "org_a", "tenant_id": "tenant_a"},
                    {"organization_id": "org_b", "tenant_id": "tenant_b"},
                ],
                "role_permissions": {
                    "admin": ["memory:read", "memory:delete"],
                    "viewer": ["memory:read"],
                },
                "refresh_seconds": 5,
                "stale_after_seconds": 20,
            },
        }
    )


def provision(host, issuer, *, enabled=True):
    p = Principal(
        principal_id="local_admin",
        home_scope=Scope(
            tenant_id="tenant_a", application_id="app", user_id="admin", agent_id="agent"
        ),
        permissions=tuple(Permission),
        auth_epoch=1,
    )
    host.identity.provision(
        [(sha256(b"static").hexdigest(), p)],
        tenants={"tenant_a": enabled, "tenant_b": True},
        jwt_issuers=[issuer.model_dump(mode="json")],
    )


@pytest.fixture
def host(tmp_path, issuer):
    host = Foundation(tmp_path / "p3.db")
    state = {"now": "2026-10-01T00:00:00.000000Z"}
    host.identity.clock = lambda: state["now"]
    provision(host, issuer)
    yield host, state
    host.close()


def record(tenant="tenant_a", permissions=(Permission.READ, Permission.DELETE)):
    return {
        "principal": Principal(
            principal_id="kc_" + tenant,
            home_scope=Scope(
                tenant_id=tenant, application_id="app", user_id="kc_alice", agent_id="agent"
            ),
            permissions=permissions,
            auth_epoch=1,
        ),
        "subject": "alice",
        "details": {
            "organization_id": "org_" + tenant[-1],
            "organization_name": tenant,
            "username": "alice",
            "roles": ["admin" if len(permissions) > 1 else "viewer"],
        },
    }


def auth(host, issuer, tenant=None):
    return host.identity.authenticate_subject(
        issuer.issuer, "alice", jwt_issuer_policy_hash(issuer.model_dump(mode="json")), tenant
    )


def snapshot(host, issuer, records):
    return store_snapshot(host.identity, issuer, records, host.identity.clock())


def test_same_account_roles_are_scoped_to_selected_organization(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record(), record("tenant_b", (Permission.READ,))])
    a, b = auth(host, issuer, "tenant_a"), auth(host, issuer, "tenant_b")
    assert Permission.DELETE in a.permissions and Permission.DELETE not in b.permissions
    assert a.principal_id != b.principal_id
    with pytest.raises(FoundationError) as ambiguous:
        auth(host, issuer)
    assert ambiguous.value.code == ErrorCode.FORBIDDEN
    assert len(host.identity.directory_details(a)["organizations"]) == 2
    assert host.identity.authenticate("static").principal_id == "local_admin"


def test_ambiguous_organization_never_selects_default_from_snapshot_order(host, issuer):
    host, _ = host
    for records in ([record(), record("tenant_b")], [record("tenant_b"), record()]):
        snapshot(host, issuer, records)
        with pytest.raises(FoundationError) as error:
            auth(host, issuer)
        assert error.value.code == ErrorCode.FORBIDDEN


def test_disabled_second_tenant_does_not_make_active_identity_ambiguous(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record(), record("tenant_b")])
    with host.uow.transaction() as tx:
        tx.write("settings", "business_tenants", {"tenant_a": True, "tenant_b": False})
    assert auth(host, issuer).home_scope.tenant_id == "tenant_a"


def test_selector_does_not_grant_membership(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record()])
    with pytest.raises(FoundationError) as error:
        auth(host, issuer, "tenant_b")
    assert error.value.code == ErrorCode.FORBIDDEN


def test_removed_member_and_inflight_context_are_revoked(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record()])
    ctx = host.identity.context_for_principal(auth(host, issuer))
    snapshot(host, issuer, [])
    with pytest.raises(FoundationError):
        auth(host, issuer)
    with pytest.raises(FoundationError) as error, host.uow.transaction() as tx:
        host.identity.revalidate(tx, ctx)
    assert error.value.code == ErrorCode.FORBIDDEN


def test_role_downgrade_advances_epoch_and_fences_prior_work(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record()])
    before = auth(host, issuer)
    ctx = host.identity.context_for_principal(before)
    snapshot(host, issuer, [record(permissions=(Permission.READ,))])
    after = auth(host, issuer)
    assert after.auth_epoch == before.auth_epoch + 1
    assert Permission.DELETE not in after.permissions
    with pytest.raises(FoundationError), host.uow.transaction() as tx:
        host.identity.revalidate(tx, ctx)


def test_stable_refresh_keeps_epoch(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record()])
    before = auth(host, issuer)
    snapshot(host, issuer, [record()])
    assert auth(host, issuer) == before


def test_removed_then_readded_member_cannot_resume_old_context(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record()])
    before = auth(host, issuer)
    snapshot(host, issuer, [])
    snapshot(host, issuer, [record()])
    assert auth(host, issuer).auth_epoch > before.auth_epoch


def test_expired_directory_fails_closed_but_static_login_survives(host, issuer):
    host, clock = host
    snapshot(host, issuer, [record()])
    ctx = host.identity.context_for_principal(auth(host, issuer))
    clock["now"] = "2026-10-01T00:00:21.000000Z"
    with pytest.raises(FoundationError) as error:
        auth(host, issuer)
    assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    with host.uow.transaction() as tx, pytest.raises(FoundationError):
        host.identity.revalidate(tx, ctx)
    assert host.identity.authenticate("static").principal_id == "local_admin"


def test_static_configuration_reload_preserves_native_directory_rows(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record()])
    provision(host, issuer)
    assert auth(host, issuer).home_scope.tenant_id == "tenant_a"


def test_disabled_tenant_never_reenabled_by_directory_refresh(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record()])
    # Disable via trusted configuration without altering the static tenant A principal's epoch.
    with host.uow.transaction() as tx:
        tx.write("settings", "business_tenants", {"tenant_a": False, "tenant_b": True})
    snapshot(host, issuer, [record()])
    with pytest.raises(FoundationError):
        auth(host, issuer)


def test_stale_inflight_collection_cannot_override_new_policy(host, issuer):
    host, _ = host
    new = issuer.model_copy(update={"audience": "another-audience"})
    provision(host, new)
    assert not snapshot(host, issuer, [record()])
    with pytest.raises(FoundationError):
        auth(host, issuer)


def test_failed_duplicate_snapshot_is_atomic(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record()])
    with pytest.raises(DirectoryUnavailableError):
        snapshot(
            host,
            issuer,
            [record(permissions=(Permission.READ,)), record(permissions=(Permission.READ,))],
        )
    assert Permission.DELETE in auth(host, issuer).permissions


def test_disabling_directory_revokes_native_identities(host, issuer):
    host, _ = host
    snapshot(host, issuer, [record()])
    host.identity.provision([], tenants={"tenant_a": True, "tenant_b": True})
    with pytest.raises(FoundationError):
        auth(host, issuer)


def test_directory_reads_native_org_group_roles_without_global_role_union(issuer):
    paths = []

    def respond(request):
        path = request.url.path.removeprefix("/admin/realms/p3")
        paths.append(path)
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "test-readonly-token"})
        if path == "/clients":
            return httpx.Response(200, json=[{"id": "client"}])
        tenant = "org_a" if "/org_a" in path else "org_b"
        if path.endswith("/members"):
            value = [{"id": "alice", "username": "alice", "enabled": True}]
        elif "/members/alice/groups" in path:
            value = [{"id": "group"}]
        elif path.endswith("/composite"):
            value = [{"name": "admin" if tenant == "org_a" else "viewer"}]
        elif path.endswith("/groups/group"):
            value = {"id": "group", "parentId": None}
        else:
            value = {"id": tenant, "name": tenant, "enabled": True}
        return httpx.Response(200, json=value)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        reader = KeycloakReader(issuer, client=client)
        records = reader.fetch()
    assert len(records) == 2
    assert Permission.DELETE in records[0]["principal"].permissions
    assert Permission.DELETE not in records[1]["principal"].permissions
    assert all("/users/alice/role-mappings" not in p for p in paths)


def test_directory_read_failure_does_not_return_partial_membership(issuer):
    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "test-token"})
        return httpx.Response(403, json={"error": "forbidden"})

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(DirectoryUnavailableError),
    ):
        KeycloakReader(issuer, client=client).fetch()


@pytest.mark.parametrize(
    "url",
    [
        "http://identity.example/realms/p3",
        "https://x/realms/a?secret=bad",
        "https://u:p@x/realms/a",
    ],
)
def test_directory_requires_safe_issuer(issuer, url):
    data = issuer.model_dump(mode="json")
    data["issuer"] = url
    with pytest.raises(ValidationError):
        JWTIssuerConfiguration.model_validate(data)
