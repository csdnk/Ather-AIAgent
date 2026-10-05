from types import SimpleNamespace

import pytest
from test_flows import app as app
from test_generation_assembly import assembly_setup

from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.flows.vector_adapters import MilvusVectors
from azure_test_runtime import Foundation, create_runtime
from controlled_embedding import TEST_SPACE as SPACE


def test_generation_bootstrap_requires_index_readiness_provider(app):
    _, assembly, body, _ = assembly_setup(app)
    app.model_space = app.recall.model_space = "test_space"
    app.embedding = assembly.candidates.embedding
    without_readiness = SimpleNamespace(
        load=body.load, working=body.working, final_guard=app.remember.final_guard
    )
    with pytest.raises(ValueError, match="complete B"):
        app.enable_generation_recall(
            memories=without_readiness,
            qualification=assembly.candidates.qualification,
            bodies=body,
            guards=body,
            space=assembly.candidates.spaces.resolve("test_space"),
        )


@pytest.mark.parametrize("invalid", ["body_provider", "model_space"])
def test_generation_bootstrap_still_requires_body_and_model_contracts(app, invalid):
    _, assembly, body, _ = assembly_setup(app)
    app.model_space = app.recall.model_space = "test_space"
    app.embedding = assembly.candidates.embedding
    body.final_guard = app.remember.final_guard
    space = assembly.candidates.spaces.resolve("test_space")
    if invalid == "model_space":
        space = space.model_copy(update={"model_space": "unloaded_model"})
    with pytest.raises(ValueError, match="complete B|space must match"):
        app.enable_generation_recall(
            memories=body,
            qualification=assembly.candidates.qualification,
            bodies=SimpleNamespace() if invalid == "body_provider" else body,
            guards=body,
            space=space,
        )


def test_injected_milvus_working_projection_and_recall_use_same_backend(tmp_path, temporal_server):
    from hashlib import sha256

    from remember_helpers import context, drain, recall
    from test_milvus_adapter import Client
    from test_remember_pipeline import observe

    from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
    from temporal_test_support import seed_driver

    database = tmp_path / "injected.db"
    foundation = Foundation(database)
    client = Client()
    vectors = MilvusVectors(
        foundation.uow, foundation.identity, SPACE, 256, uri="test", client=client
    )
    # This test isolates composition; collection preparation has separate contract coverage.
    vectors.prepared = True
    host = None
    try:
        host = create_runtime(
            database, tmp_path / "cache", vectors=vectors, embedding_profile="injected"
        )
        principal = Principal(
            principal_id="alice",
            home_scope=Scope(
                tenant_id="t1", application_id="app", user_id="alice", agent_id="agent"
            ),
            permissions=tuple(Permission),
            auth_epoch=1,
        )
        host.foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
        seed_driver(host, temporal_server)
        receipt = observe(host, "remember the deployment location")
        drain(host)
        pack = recall(
            host,
            query="deployment location",
            sources="working",
            selection=ScopeSelector(session_id="session_1"),
        )
        assert pack.outcome == "available"
        assert {item.memory for group in pack.groups for item in group.items} == set(
            receipt.memories
        )
        with host.foundation.uow.transaction() as tx:
            assert not tx.rows("generation_vectors")
            assert not tx.rows("recall_vectors")
        assert host.remember.get(context(host), receipt.memories[0].memory_id).projection_state == (
            "ready"
        )
        host.close()
        assert not vectors.closed  # Injection does not transfer ownership.
    finally:
        if host is not None:
            host.close()
        vectors.close()
        foundation.close()
