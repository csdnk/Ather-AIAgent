"""Remote bodies keep provider binding across instances without local authority."""

from hashlib import sha256

import pytest

from aether_agent_memory.remember.basic.content import Bodies
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.runtime.contracts.models import ErrorCode, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError
from azure_test_runtime import create_runtime


class RemoteObjects:
    endpoint = "p2.internal:50051"
    bucket = "test-memory"

    def __init__(self):
        self.data = {}

    def put_object_sync(self, key, value):
        if key in self.data and self.data[key] != value:
            raise ValueError("immutable key conflict")
        self.data[key] = value

    def get_object_sync(self, key):
        return self.data.get(key)


def test_remote_body_binding_does_not_depend_on_worker_local_directory(tmp_path):
    objects = RemoteObjects()
    database = tmp_path / "test.db"
    first = create_runtime(
        database,
        tmp_path / "a-cache",
        body_root=tmp_path / "a-body",
        embedding_profile="injected",
        p2=objects,
    )
    first.close()
    second = create_runtime(
        database,
        tmp_path / "b-cache",
        body_root=tmp_path / "b-body",
        embedding_profile="injected",
        p2=objects,
    )
    second.close()


def test_remote_body_hash_and_existence_are_checked_even_with_local_spool(tmp_path):
    objects = RemoteObjects()
    bodies = Bodies(tmp_path / "derived", RememberPolicy(), p2=objects)
    scope = Scope(tenant_id="t1", application_id="a1", user_id="u1", agent_id="bot")
    location = bodies.stage(scope, "durable body")
    assert bodies.read_local(location) == "durable body"
    bodies.path(location).write_text("durable body", encoding="utf-8")
    objects.data.clear()
    with pytest.raises(FoundationError) as error:
        bodies.read_local(location)
    assert error.value.code in {ErrorCode.NOT_FOUND, ErrorCode.DEPENDENCY_UNAVAILABLE}
    objects.data[location.object_key] = b"tampered"
    with pytest.raises(FoundationError) as error:
        bodies.read_local(location)
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


def test_pg_remote_body_reopens_without_spool_and_reads_outside_transaction(tmp_path):
    import os
    from uuid import uuid4

    from aether_agent_memory.remember.contracts.models import (
        RememberRequest,
        SourceInput,
        TextInput,
    )
    from aether_agent_memory.runtime.contracts.models import Permission, Principal, ScopeSelector
    from aether_agent_memory.runtime.foundation.common import now
    from aether_agent_memory.runtime.foundation.requests import text_hash

    dsn = os.environ.get("P3_TEST_STATE_DSN")
    assert dsn, "real PostgreSQL is required"
    objects = RemoteObjects()
    first = create_runtime(
        tmp_path / "not-created.db",
        tmp_path / "a-cache",
        body_root=tmp_path / "a-body",
        postgres_dsn=dsn,
        embedding_profile="injected",
        p2=objects,
    )
    token = uuid4().hex
    principal = Principal(
        principal_id="p-" + uuid4().hex,
        auth_epoch=1,
        permissions=tuple(Permission),
        home_scope=Scope(tenant_id="remote", application_id="app", user_id="u", agent_id="bot"),
    )
    first.foundation.identity.provision([(sha256(token.encode()).hexdigest(), principal)])
    ctx = first.foundation.identity.context(token)
    request = RememberRequest(
        source=SourceInput(
            kind="conversation", external_id=uuid4().hex, external_version="1", occurred_at=now()
        ),
        selection=ScopeSelector(session_id="session"),
        content=TextInput(kind="text", text="遠端 postgres Ceph immutable source"),
    )

    # The port implementation verifies bytes before publication.
    async def get_object(key):
        return objects.get_object_sync(key)

    async def put_object(key, data):
        objects.put_object_sync(key, data)

    objects.get_object, objects.put_object = get_object, put_object
    import asyncio

    receipt = asyncio.run(first.remember.save(ctx, request))
    first.close()
    assert not list((tmp_path / "a-body").iterdir())
    second = create_runtime(
        tmp_path / "not-created.db",
        tmp_path / "b-cache",
        body_root=tmp_path / "b-body",
        postgres_dsn=dsn,
        embedding_profile="injected",
        p2=objects,
    )
    original = objects.get_object_sync
    calls = []

    def no_transaction_read(key):
        with second.foundation.uow.transaction() as tx:
            tx.write("remote_read_probe", uuid4().hex, {"ok": True})
        calls.append(key)
        return original(key)

    objects.get_object_sync = no_transaction_read
    try:
        ctx = second.foundation.identity.context(token)
        item = second.remember.get(ctx, receipt.memories[0].memory_id)
        assert item.content == request.content.text
        assert text_hash(item.content) == item.content_hash
        assert calls
        assert not list((tmp_path / "b-body").iterdir())
    finally:
        second.close()


def test_async_hydration_does_not_replay_external_effects(tmp_path):
    import asyncio

    from aether_agent_memory.remember.basic.hydration import (
        BodyReadRequiredError,
        hydrate_metadata_reads,
    )
    from aether_agent_memory.remember.contracts.models import MemoryRef
    from aether_agent_memory.runtime.contracts.models import Principal, TrustedContext

    scope = Scope(tenant_id="t", application_id="a", user_id="u", agent_id="b")
    bodies = Bodies(tmp_path / "body", RememberPolicy())
    calls = []

    @hydrate_metadata_reads
    class Domain:
        def __init__(self):
            self.bodies = bodies

        async def generate(self, ctx):
            calls.append("remote model already completed")
            raise BodyReadRequiredError(
                MemoryRef(scope=scope, memory_id="m", version=1),
                bodies.location(scope, "immutable"),
            )

    context = TrustedContext(
        principal=Principal(principal_id="p", auth_epoch=1, home_scope=scope, permissions=()),
        operation_id="op",
        request_id="request",
        trace_id="1" * 32,
        span_id="2" * 16,
        deadline_at="2099-01-01T00:00:00.000Z",
    )
    with pytest.raises(BodyReadRequiredError):
        asyncio.run(Domain().generate(context))
    assert len(calls) == 1


def test_pg_authority_never_writes_local_replica_even_on_reconciliation(tmp_path):
    objects = RemoteObjects()
    bodies = Bodies(tmp_path / "body", RememberPolicy(), p2=objects)
    bodies.remote_only = True
    scope = Scope(tenant_id="t", application_id="a", user_id="u", agent_id="b")
    location = bodies.location(scope, "same verified bytes")
    bodies._spool(location, "same verified bytes")
    assert not list(bodies.root.iterdir())
    assert bodies.verified_text(location) == "same verified bytes"


def test_guard_checks_metadata_without_fetching_body_after_restart(tmp_path):
    import asyncio
    import os
    from uuid import uuid4

    from aether_agent_memory.remember.contracts.models import (
        RememberRequest,
        SourceInput,
        TextInput,
    )
    from aether_agent_memory.runtime.contracts.models import Permission, Principal, ScopeSelector
    from aether_agent_memory.runtime.foundation.common import now

    dsn = os.environ.get("P3_TEST_STATE_DSN")
    assert dsn, "real PostgreSQL is required"
    objects = RemoteObjects()

    async def get_object(key):
        return objects.get_object_sync(key)

    async def put_object(key, data):
        objects.put_object_sync(key, data)

    objects.get_object, objects.put_object = get_object, put_object
    scope = Scope(tenant_id="guard-" + uuid4().hex, application_id="a", user_id="u", agent_id="b")
    token = uuid4().hex
    principal = Principal(
        principal_id="p-" + uuid4().hex,
        auth_epoch=1,
        home_scope=scope,
        permissions=tuple(Permission),
    )
    options = dict(postgres_dsn=dsn, embedding_profile="injected", p2=objects)
    first = create_runtime(tmp_path / "absent.db", tmp_path / "a-cache", **options)
    first.foundation.identity.provision([(sha256(token.encode()).hexdigest(), principal)])
    ctx = first.foundation.identity.context(token)
    receipt = asyncio.run(
        first.remember.save(
            ctx,
            RememberRequest(
                selection=ScopeSelector(session_id="s"),
                source=SourceInput(
                    kind="conversation",
                    external_id=uuid4().hex,
                    external_version="1",
                    occurred_at=now(),
                ),
                content=TextInput(kind="text", text="metadata guard must not require Ceph"),
            ),
        )
    )
    first.close()
    second = create_runtime(tmp_path / "absent.db", tmp_path / "b-cache", **options)
    try:
        ctx = second.foundation.identity.context(token)
        with second.foundation.uow.transaction() as tx:
            guard = second.remember.final_guard(tx, ctx, receipt.memories, "recall")
        assert guard.items[0].decision == "allowed"
        assert not second.remember.bodies.verified
    finally:
        second.close()
