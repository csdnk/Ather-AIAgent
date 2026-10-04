"""Real PostgreSQL checks for scoped service tokens and durable revocation."""

import hashlib
import json
import secrets
from contextlib import closing

import pytest
from test_postgres_observability import dsns as dsns

from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    Principal,
    RecordRef,
    Scope,
)
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.postgres import PostgresUnitOfWork

pytestmark = pytest.mark.integration


def principal(name, permissions, epoch=1):
    return Principal(
        principal_id=name,
        home_scope=Scope(
            tenant_id="aether", application_id="p3", user_id="backend", agent_id="aether"
        ),
        permissions=permissions,
        auth_epoch=epoch,
    )


def provision(identity, entries, revision):
    identity.provision(
        [(hashlib.sha256(token.encode()).hexdigest(), actor) for token, actor in entries],
        tenants={"aether": True},
        configuration_revision=revision,
    )


def rejected(code, body):
    with pytest.raises(FoundationError) as result:
        body()
    assert result.value.code == code


def test_random_service_credentials_enforce_permissions_and_survive_new_client(tmp_path, dsns):
    writer_token, reader_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    writer = principal("application", (Permission.READ, Permission.WRITE))
    reader = principal("reader", (Permission.READ,))
    with closing(PostgresUnitOfWork(dsns["state"], tmp_path / "first")) as uow:
        identity = Identity(uow)
        provision(identity, [(writer_token, writer), (reader_token, reader)], 1)
        target = RecordRef(
            owner=Flow.REMEMBER, object_type="memory", object_id="original", scope=writer.home_scope
        )
        writer_ctx, reader_ctx = identity.context(writer_token), identity.context(reader_token)
        with uow.transaction() as tx:
            identity.authorize(tx, writer_ctx, Permission.WRITE, target)
            identity.authorize(tx, reader_ctx, Permission.READ, target)
            assert not identity.permits(tx, reader_ctx, Permission.WRITE, target)
            foreign = target.model_copy(
                update={"scope": target.scope.model_copy(update={"tenant_id": "another_tenant"})}
            )
            assert not identity.permits(tx, writer_ctx, Permission.READ, foreign)
            persisted = json.dumps(tx.rows("identities"))
            assert writer_token not in persisted and reader_token not in persisted
        for known_demo_credential in ("alice", "eve", "bootstrap_admin", "local_admin"):
            rejected(
                ErrorCode.UNAUTHENTICATED,
                lambda value=known_demo_credential: identity.context(value),
            )
    with closing(PostgresUnitOfWork(dsns["state"], tmp_path / "reconstructed")) as uow:
        identity = Identity(uow)
        assert identity.context(writer_token).principal == writer
        assert identity.context(reader_token).principal == reader


def test_rotation_revocation_and_stale_configuration_are_fenced_across_clients(tmp_path, dsns):
    original_token, rotated_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    actor = principal("application", (Permission.READ, Permission.WRITE))
    first = PostgresUnitOfWork(dsns["state"], tmp_path / "first")
    second = PostgresUnitOfWork(dsns["state"], tmp_path / "second")
    try:
        identity, other = Identity(first), Identity(second)
        provision(identity, [(original_token, actor)], 1)
        original_context = other.context(original_token)
        rotated = actor.model_copy(update={"auth_epoch": 2})
        provision(identity, [(rotated_token, rotated)], 2)
        rejected(ErrorCode.UNAUTHENTICATED, lambda: other.context(original_token))
        with pytest.raises(FoundationError) as result, second.transaction() as tx:
            tx.write("identity_probe", "old-result", {"must": "roll back"})
            tx.before_commit.append(lambda: other.revalidate(tx, original_context))
        assert result.value.code == ErrorCode.FORBIDDEN
        with second.transaction() as tx:
            assert tx.read("identity_probe", "old-result") is None
        assert other.context(rotated_token).principal.auth_epoch == 2
        with pytest.raises(ValueError, match="configuration must advance"):
            provision(other, [(original_token, actor)], 1)
        provision(identity, [], 3)
        rejected(ErrorCode.UNAUTHENTICATED, lambda: other.context(rotated_token))
        with pytest.raises(ValueError, match="strictly increasing auth_epoch"):
            provision(other, [(rotated_token, rotated)], 4)
    finally:
        second.close()
        first.close()
    with closing(PostgresUnitOfWork(dsns["state"], tmp_path / "reconstructed")) as uow:
        identity = Identity(uow)
        rejected(ErrorCode.UNAUTHENTICATED, lambda: identity.context(original_token))
        rejected(ErrorCode.UNAUTHENTICATED, lambda: identity.context(rotated_token))
