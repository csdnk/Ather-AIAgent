import asyncio
from hashlib import sha256

import pytest

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.contracts.models import (
    MemoryRef,
    RememberRequest,
    SourceInput,
    TextInput,
)
from aether_agent_memory.remember.local import create_runtime
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope, ScopeSelector
from aether_agent_memory.runtime.foundation.common import now


def context(app, user="alice", operation=None):
    return app.foundation.identity.context(user, timeout_seconds=300, operation_id=operation)


def source(name="input"):
    return SourceInput(
        kind="conversation", external_id=name, external_version="1", occurred_at=now()
    )


@pytest.fixture
def app(tmp_path):
    host = create_runtime(tmp_path / "p3.db", tmp_path / "cache", embedding_profile="lexical")
    people = [
        Principal(
            principal_id=user,
            home_scope=Scope(
                tenant_id=tenant, application_id="app", user_id=user, agent_id="agent"
            ),
            permissions=tuple(Permission),
            auth_epoch=1,
        )
        for user, tenant in [("alice", "t1"), ("bob", "t1"), ("carol", "t2"), ("david", "t2")]
    ]
    host.foundation.identity.provision(
        [(sha256(p.principal_id.encode()).hexdigest(), p) for p in people]
    )
    yield host
    host.close()


def save(app, text="我喜欢无糖咖啡", session="session_1", user="alice", operation=None):
    return asyncio.run(
        app.remember.save(
            context(app, user, operation),
            RememberRequest(
                source=source(),
                selection=ScopeSelector(session_id=session),
                content=TextInput(kind="text", text=text),
            ),
        )
    )


def drain(app):
    asyncio.run(app.drain())


def facts(app, receipt):
    ctx = context(app)
    task = app.foundation.diagnostics.task(ctx, receipt.task_ids[0])
    assert task.state == "succeeded", task
    with app.foundation.uow.transaction() as tx:
        return [MemoryRef.model_validate(ref) for ref in tx.get(task.result_ref)["memories"]]


def recall(
    app,
    query="我喜欢无糖咖啡",
    sources="long_term",
    user="alice",
    operation=None,
    budget=1024,
    selection=None,
):
    return asyncio.run(
        app.recall.recall(
            context(app, user, operation),
            RecallRequest(
                query=query,
                selection=selection or ScopeSelector(),
                sources=sources,
                token_budget=budget,
            ),
        )
    )
