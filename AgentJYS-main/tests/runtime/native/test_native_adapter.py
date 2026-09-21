"""P3 adapter contract/failure tests use a deterministic native backend double."""

import asyncio
from hashlib import sha256

import pytest

from aether_agent_memory.recall.contracts.models import EmbeddingRequest
from aether_agent_memory.recall.embedding import p3
from aether_agent_memory.recall.embedding.service import ComputedEmbedding, SemanticEmbeddingError
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.flows.host import ThreeFlows
from aether_agent_memory.runtime.foundation.common import FoundationError, later, now


class Backend:
    max_input_tokens = 512
    calls = []
    closed_count = 0
    change = None
    failure = None
    delay = 0
    model_version = "a" * 64

    def __init__(self, settings):
        self.binding = None

    def describe(self):
        return {
            "model_id": "BAAI/bge-small-zh-v1.5",
            "model_version": self.model_version,
            "dimension": 512,
            "dtype": "float32",
            "embedding_schema_version": "bge-v1",
            "preprocessing_version": "b" * 64,
        }

    def bind(self, binding, *, records):
        self.binding = binding

    def count_tokens(self, text, usage):
        return len(text) + 2

    async def compute(self, request, text):
        type(self).calls.append((request, text))
        if type(self).change:
            type(self).change()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise SemanticEmbeddingError(self.failure)
        return ComputedEmbedding(
            vector=[1.0] + [0.0] * 511,
            usage=request.usage,
            model_binding=self.binding,
            evidence_ref="test_evidence",
        )

    def shutdown(self):
        type(self).closed_count += 1


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(p3, "NativeEmbeddingBackend", Backend)
    monkeypatch.setattr(Backend, "calls", [])
    monkeypatch.setattr(Backend, "change", None)
    monkeypatch.setattr(Backend, "failure", None)
    monkeypatch.setattr(Backend, "delay", 0)
    host = ThreeFlows(tmp_path / "p3.db", tmp_path / "cache")
    host.foundation.identity.provision(
        [
            (
                sha256(user.encode()).hexdigest(),
                Principal(
                    principal_id=user,
                    home_scope=Scope(
                        tenant_id="t1", application_id="app", user_id=user, agent_id="agent"
                    ),
                    permissions=tuple(Permission),
                    auth_epoch=1,
                ),
            )
            for user in ("alice", "bob")
        ]
    )
    yield host
    host.close()


def context(app, user="alice"):
    return app.foundation.identity.context(user)


def request(app, ctx, text="无糖咖啡", operation="test", usage="query", **updates):
    return EmbeddingRequest(
        operation_id=operation,
        usage=usage,
        texts=(text,),
        model_space=app.model_space,
        deadline_at=ctx.deadline_at,
        **updates,
    )


def test_native_is_default_with_shared_space_and_separate_query_passage(app):
    ctx = context(app)
    query = asyncio.run(app.embedding.embed(ctx, request(app, ctx)))
    passage = asyncio.run(app.embedding.embed(ctx, request(app, ctx, usage="passage")))
    assert app.embedding_profile == "native"
    assert query.dimensions == passage.dimensions == app.vectors.dimensions == 512
    assert (
        query.model_space
        == passage.model_space
        == app.remember.model_space
        == app.recall.model_space
    )
    assert [r.usage for r, _ in Backend.calls] == ["Query", "Passage"]
    assert app.native_embedding.backends["Query"] is not app.native_embedding.backends["Passage"]
    with app.foundation.uow.transaction() as tx:
        assert len(tx.rows("native_embedding_attempts")) == 2
        assert tx.raw.scan("semantic-execution") == []


def test_operation_fingerprint_rejects_changed_text_but_isolates_users(app):
    ctx = context(app)
    asyncio.run(app.embedding.embed(ctx, request(app, ctx)))
    with pytest.raises(FoundationError) as error:
        asyncio.run(app.embedding.embed(ctx, request(app, ctx, text="不同内容")))
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    other = context(app, "bob")
    assert asyncio.run(app.embedding.embed(other, request(app, other, text="不同内容")))


def test_revoked_identity_cannot_receive_computed_vector(app, monkeypatch):
    ctx = context(app)
    monkeypatch.setattr(Backend, "change", lambda: app.foundation.identity.provision([]))
    with pytest.raises(FoundationError) as error:
        asyncio.run(app.embedding.embed(ctx, request(app, ctx)))
    assert error.value.code == "FORBIDDEN"
    with app.foundation.uow.transaction() as tx:
        assert all(r["state"] == "failed" for _, r in tx.rows("native_embedding_attempts"))


def test_native_failure_never_falls_back_to_lexical(app, monkeypatch):
    monkeypatch.setattr(Backend, "failure", "EMBEDDING_COMPUTE_FAILED")
    ctx = context(app)
    with pytest.raises(FoundationError) as error:
        asyncio.run(app.embedding.embed(ctx, request(app, ctx)))
    assert error.value.code == "DEPENDENCY_UNAVAILABLE"
    assert len(Backend.calls) == 1


def test_native_deadline_is_enforced(app, monkeypatch):
    monkeypatch.setattr(Backend, "delay", 1)
    ctx = context(app)
    req = request(app, ctx).model_copy(update={"deadline_at": later(now(), 0.02)})
    with pytest.raises(FoundationError) as error:
        asyncio.run(app.embedding.embed(ctx, req))
    assert error.value.code == "DEADLINE_EXCEEDED"


def test_long_input_rejected_without_truncation_or_backend_call(app):
    ctx = context(app)
    with pytest.raises(FoundationError) as error:
        asyncio.run(app.embedding.embed(ctx, request(app, ctx, text="长" * 513)))
    assert error.value.code == "INVALID_ARGUMENT" and not Backend.calls


def test_changed_model_rejected_without_relabeling_existing_database(app, monkeypatch):
    with app.foundation.uow.transaction() as tx:
        old = tx.read("settings", "p3_embedding_binding")
    monkeypatch.setattr(Backend, "model_version", "c" * 64)
    with pytest.raises(FoundationError):
        ThreeFlows(app.foundation.uow.path, app.executor.root)
    with app.foundation.uow.transaction() as tx:
        assert tx.read("settings", "p3_embedding_binding") == old


def test_native_database_cannot_silently_switch_to_lexical(app):
    with pytest.raises(ValueError):
        ThreeFlows(app.foundation.uow.path, app.executor.root, embedding_profile="lexical")
