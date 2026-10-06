"""P3 adapter keeps object scope and invalidates durable work after authority changes."""

import copy
import hashlib
from contextlib import contextmanager

import pytest
from test_ruoyi_identity import fixture

from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.transactions import StorageTransaction


class Records:
    def __init__(self):
        self.data = {}

    def get(self, namespace, tenant, key):
        return self.data.get((namespace, tenant, key))

    def put(self, namespace, tenant, key, value):
        self.data[namespace, tenant, key] = value

    def scan(self, namespace):
        return [(t, k, v) for (n, t, k), v in self.data.items() if n == namespace]


class MemoryUow:
    """Test transaction transport; production Identity and StorageTransaction run unchanged."""

    def __init__(self):
        self.records = Records()

    @contextmanager
    def transaction(self):
        before = copy.deepcopy(self.records.data)
        tx = StorageTransaction(self.records, "test-cursor-key")
        try:
            yield tx
            for callback in tx.before_commit:
                callback()
        except BaseException:
            self.records.data = before
            raise


def setup(tmp_path):
    from aether_agent_memory.runtime.flows.ruoyi_auth import RuoyiAuthenticator

    verifier, state, token, config = fixture(tmp_path)
    config["ruoyi"]["role_permissions"] = {"aether_user": ["memory:read", "memory:write"]}
    config["ruoyi"]["mappings"][0].update(
        principal_id="old-principal", application_id="app", agent_id="agent"
    )
    identity = Identity(MemoryUow())
    adapter = RuoyiAuthenticator(identity, config["ruoyi"], client=verifier.http)
    return adapter, identity, state, token


def test_p3_uses_original_scope_and_rejects_foreign_tenant(tmp_path):
    from aether_agent_memory.runtime.foundation.common import FoundationError

    adapter, identity, state, token = setup(tmp_path)
    principal = adapter.authenticate(identity, "opaque-token")
    assert principal.principal_id == "old-principal"
    assert principal.home_scope.user_id == "old-user"
    assert principal.home_scope.tenant_id == "old-tenant"
    with pytest.raises(FoundationError):
        adapter.authenticate(identity, "opaque-token", "foreign")


def test_p3_durable_context_rechecks_live_authority_without_raw_token(tmp_path):
    from aether_agent_memory.runtime.foundation.common import FoundationError

    adapter, identity, state, token = setup(tmp_path)
    principal = adapter.authenticate(identity, "opaque-token")
    ctx = identity.context_for_principal(principal)
    adapter.bind_context(ctx, "opaque-token")
    with identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)
        assert "opaque-token" not in str(tx.rows("identities"))
        assert hashlib.sha256(b"opaque-token").hexdigest() in str(tx.rows("ruoyi_request_grants"))
    state["role_codes"] = []
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)


def test_new_ruoyi_user_gets_scoped_p3_principal(tmp_path):
    adapter, identity, state, token = setup(tmp_path)
    adapter.verifier.config["auto_provision"] = True
    state["user_id"] = token["user_id"] = 99
    p = adapter.authenticate(identity, "opaque-token")
    assert p.principal_id == "ry_principal_99"
    assert p.home_scope.user_id == "ry_user_99"
    assert p.home_scope.tenant_id == "old-tenant"


def test_identity_configuration_accepts_explicit_ruoyi_authority(tmp_path):
    from aether_agent_memory.runtime.flows.config import IdentityConfiguration

    _, _, _, config = fixture(tmp_path)
    config["ruoyi"]["role_permissions"] = {"aether_user": ["memory:read"]}
    result = IdentityConfiguration.model_validate(
        {"revision": 1, "tenants": [], "identities": [], "ruoyi": config["ruoyi"]}
    )
    assert result.ruoyi["client_id"] == "aether"


def test_ruoyi_context_survives_same_configuration_reload(tmp_path):
    adapter, identity, state, token = setup(tmp_path)
    principal = adapter.authenticate(identity, "opaque-token")
    identity.provision([], tenants={}, ruoyi_policy=adapter.policy)
    ctx = identity.context_for_principal(principal)
    adapter.bind_context(ctx, "opaque-token")
    with identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)


def test_two_tokens_remain_valid_and_revocation_is_independent(tmp_path):
    from aether_agent_memory.runtime.foundation.common import FoundationError
    from aether_platform.directory import AccessDeniedError

    adapter, identity, state, token = setup(tmp_path)
    first = adapter.authenticate(identity, "first-token")
    first_ctx = identity.context_for_principal(first)
    adapter.bind_context(first_ctx, "first-token")
    second = adapter.authenticate(identity, "second-token")
    second_ctx = identity.context_for_principal(second)
    adapter.bind_context(second_ctx, "second-token")
    assert first == second
    with identity.uow.transaction() as tx:
        identity.revalidate(tx, first_ctx)
        identity.revalidate(tx, second_ctx)
    original = adapter.verifier.remote

    def remote(method, path, **kwargs):
        if kwargs.get("data", {}).get("token_hash") == hashlib.sha256(b"first-token").hexdigest():
            raise AccessDeniedError("Revoked token")
        return original(method, path, **kwargs)

    adapter.verifier.remote = remote
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, first_ctx)
    with identity.uow.transaction() as tx:
        identity.revalidate(tx, second_ctx)


def test_dynamic_platform_maintenance_requires_live_grant_and_preserves_memory_scope(tmp_path):
    from aether_agent_memory.runtime.contracts.models import Flow, Permission, RecordRef, Scope
    from aether_agent_memory.runtime.foundation.common import FoundationError

    adapter, identity, state, token = setup(tmp_path)
    adapter.verifier.config["auto_provision"] = True
    adapter.roles["aether_platform_admin"] = (
        Permission.DIAGNOSE,
        Permission.RECOVER,
        Permission.CONFIGURE,
    )
    state.update(
        user_id="199",
        role_codes=["aether_platform_admin"],
        permissions=["aether:ops:read", "aether:tasks:execute"],
    )
    token["user_id"] = 199
    p = adapter.authenticate(identity, "admin-token")
    ctx = identity.context_for_principal(p)
    adapter.bind_context(ctx, "admin-token")
    with identity.uow.transaction() as tx:
        assert identity.is_maintenance_operator(tx, ctx, Permission.RECOVER, ())
        assert not identity.is_maintenance_operator(tx, ctx, Permission.CONFIGURE, ())
        target = RecordRef(
            owner=Flow.REMEMBER,
            object_type="memory",
            object_id="private",
            scope=Scope(
                tenant_id="old-tenant", application_id="app", user_id="old-user", agent_id="agent"
            ),
        )
        assert not identity.permits(tx, ctx, Permission.READ, target)
    state["permissions"] = ["aether:ops:read"]
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.is_maintenance_operator(tx, ctx, Permission.RECOVER, ())
