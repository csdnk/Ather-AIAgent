"""P3 adapter contract/failure tests use a deterministic native backend double."""
# 原生适配回归使用可控后端隔离模型加载成本，检验空间绑定、撤权、期限和错误语义。
# 真实 BGE 推理证据由专用 native 验收脚本提供，不由这些替身测试代替。

import asyncio
from hashlib import sha256

import pytest

from aether_agent_memory.recall.contracts.models import EmbeddingRequest
from aether_agent_memory.recall.embedding import p3
from aether_agent_memory.recall.embedding.service import ComputedEmbedding, SemanticEmbeddingError
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.flows.host import ThreeFlows
from aether_agent_memory.runtime.foundation.common import FoundationError, later, now
from azure_test_runtime import provider_options
from controlled_embedding import ControlledEmbedding


def open_host(path, cache_root, **kwargs):
    # Exercise the production default profile; only its native compute boundary is controlled.
    options = dict(provider_options(path))
    options.pop("p2")
    options.pop("body_cache")
    options.update(kwargs)
    return ThreeFlows(path, cache_root, **options)


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
    host = open_host(tmp_path / "p3.db", tmp_path / "cache")
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


def test_native_space_persisted_and_resolvable(app):
    # 完整空间必须可解析且持久化，供检索装配和重启一致性校验使用。
    from aether_agent_memory.recall.embedding.spaces import EmbeddingSpaces

    space = app.native_embedding.space
    assert EmbeddingSpaces((space,)).resolve(app.model_space) == space
    assert space.dimensions == 512 and space.normalization == "unit"
    with app.foundation.uow.transaction() as tx:
        assert tx.read("embedding_spaces", app.model_space) == space.model_dump(mode="json")
    with pytest.raises(FoundationError):
        EmbeddingSpaces((space,)).resolve("missing")


def test_space_reports_loaded_tokenizer_limit(monkeypatch, tmp_path):
    # 公布的上限必须来自实际后端，不能把较宽的配置值当成模型真实能力。
    monkeypatch.setattr(p3, "NativeEmbeddingBackend", Backend)
    monkeypatch.setattr(Backend, "max_input_tokens", 256)
    host = open_host(tmp_path / "limited.db", tmp_path / "cache")
    try:
        assert host.native_embedding.space.max_input_tokens == 256
    finally:
        host.close()


def test_space_drift_rejected_without_overwrite(app):
    # 同一空间标识的配置漂移必须拒绝，并保留数据库中的原绑定。
    with app.foundation.uow.transaction() as tx:
        value = tx.read("embedding_spaces", app.model_space)
        value["query_prefix"] = "changed"
        tx.write("embedding_spaces", app.model_space, value)
    with pytest.raises(FoundationError, match="configuration changed"):
        p3.NativeP3Embedding(app.foundation.uow, app.foundation.identity)
    with app.foundation.uow.transaction() as tx:
        assert tx.read("embedding_spaces", app.model_space)["query_prefix"] == "changed"


def test_query_passage_binding_mismatch_closes_backends(monkeypatch, tmp_path):
    # 两种用途不兼容时初始化失败，并释放已加载后端资源。
    class MismatchedBackend(Backend):
        seen = 0

        def describe(self):
            type(self).seen += 1
            return {**super().describe(), "model_version": str(self.seen) * 64}

    monkeypatch.setattr(p3, "NativeEmbeddingBackend", MismatchedBackend)
    MismatchedBackend.closed_count = 0
    with pytest.raises(FoundationError, match="query/passage"):
        open_host(tmp_path / "mismatch.db", tmp_path / "cache")
    assert MismatchedBackend.closed_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["index", "hash", "dimension", "norm", "operation", "usage"])
async def test_embedding_consumer_rejects_wrong_result(app, fault):
    # 分别破坏批次归属、索引、摘要或向量数据，确保消费方拒绝错误编码结果。
    from aether_agent_memory.recall.embedding.spaces import validate_embedding

    ctx = context(app)
    req = request(app, ctx)
    result = await app.embedding.embed(ctx, req)
    item = result.items[0]
    if fault == "index":
        result = result.model_copy(update={"items": (item.model_copy(update={"index": 1}),)})
    elif fault == "hash":
        result = result.model_copy(
            update={"items": (item.model_copy(update={"input_hash": "f" * 64}),)}
        )
    elif fault == "dimension":
        result = result.model_copy(update={"dimensions": 2})
    elif fault == "norm":
        result = result.model_copy(
            update={"items": (item.model_copy(update={"vector": (0.0,) * 512}),)}
        )
    elif fault == "operation":
        result = result.model_copy(update={"operation_id": "wrong"})
    else:
        result = result.model_copy(update={"usage": "passage"})
    with pytest.raises(FoundationError):
        validate_embedding(req, result, app.native_embedding.space)


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
    assert error.value.code == "DEADLINE_EXCEEDED", (
        repr(error.value),
        repr(error.value.__cause__),
        [
            (type(cause).__name__, str(cause))
            for cause in (error.value.__cause__, getattr(error.value.__cause__, "__cause__", None))
            if cause is not None
        ],
    )


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
        open_host(app.foundation.uow.path, app.foundation.uow.path.parent / "cache")
    with app.foundation.uow.transaction() as tx:
        assert tx.read("settings", "p3_embedding_binding") == old


def test_native_database_cannot_silently_switch_to_lexical(app):
    with pytest.raises(ValueError, match="bound to another embedding model space"):
        open_host(
            app.foundation.uow.path,
            app.foundation.uow.path.parent / "cache",
            embedding_profile="injected",
            embedding=ControlledEmbedding(),
        )
