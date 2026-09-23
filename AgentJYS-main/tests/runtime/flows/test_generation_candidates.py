"""Real A + RF/SQLite, strict B double. Does not implement B publication."""
# 候选消费者测试：真实 A/RF/SQLite，编码及 B 资格使用严格替身。
# 故意注入高分排除、证据错配和预算耗尽，验证 A 不会把不可信命中变成可读记忆。

import asyncio
import copy
import json
from pathlib import Path

import pytest
from test_flows import app as app
from test_flows import context

from aether_agent_memory.recall.basic.candidates import MemoryCandidates
from aether_agent_memory.recall.basic.generation_search import SQLiteGenerationSearch
from aether_agent_memory.recall.contracts.foundation import EmbeddingSpace, MemorySearchRequest
from aether_agent_memory.recall.contracts.models import EmbeddingItem, EmbeddingResult
from aether_agent_memory.recall.embedding.spaces import EmbeddingSpaces
from aether_agent_memory.remember.contracts.foundation import CandidateQualificationResult
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.requests import text_hash


def example(name):
    # 复制契约目录中的有效样例，避免某个用例篡改共享样例污染后续测试。
    root = Path(__file__).resolve().parents[3]
    cases = json.loads((root / "contracts/p3/fixtures/cases.json").read_text("utf-8"))
    return copy.deepcopy(next(c["payload"] for c in cases if c["model"] == name and c["valid"]))


class Encoder:
    # 确定性二维编码替身；calls 用于证明 Working-only 没有触发编码。
    calls = 0

    async def embed(self, ctx, request):
        self.calls += 1
        return EmbeddingResult(
            operation_id=request.operation_id,
            usage=request.usage,
            model_space=request.model_space,
            dimensions=2,
            items=(
                EmbeddingItem(index=0, input_hash=text_hash(request.texts[0]), vector=(1.0, 0.0)),
            ),
        )


class Authority:
    # B 权威数据的测试替身，与 A 向量表分离；故障开关用于模拟非法提供方响应。
    """Test-owned authoritative fixture, deliberately separate from A vector storage."""

    def __init__(self, app):
        self.app = app
        self.proofs = {}
        self.decisions = {}
        self.calls = []
        self.fault = None

    async def qualify(self, ctx, targets, purpose):
        # 先检查消费者请求，再从独立 proofs 返回凭据；不从 A 的命中数据推导授权。
        assert targets and len({t.model_dump_json() for t in targets}) == len(targets)
        assert purpose in {"recall", "extraction"}
        self.calls.append(targets)
        with self.app.foundation.uow.transaction() as tx:
            self.app.foundation.identity.revalidate(tx, ctx)
        values = []
        for target in targets:
            manifest, guard = self.proofs[target.vector_id]
            decision = self.decisions.get(target.memory.memory_id, "allowed")
            values.append(
                CandidateQualificationResult(
                    target=target,
                    decision=decision,
                    reason_code=decision,
                    manifest=manifest if decision == "allowed" else None,
                    guard=guard if decision == "allowed" else None,
                )
            )
        if self.fault == "missing":
            return tuple(values[:-1])
        if self.fault == "duplicate":
            return tuple(values + values[:1])
        if self.fault == "unavailable":
            from aether_agent_memory.runtime.contracts.models import ErrorCode

            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "test failure")
        return tuple(values)


def setup(app, memories=(("m1", (0.95, 0.9)), ("m2", (0.8,)), ("m3", (0.7,)))):
    # 固定数据装载器直接准备测试索引；这不是 B 的生产发布实现。
    # 向量首维设为期望分数，配合固定 Query 向量，让分页和 Top K 断言可复现。
    ctx = context(app)
    space = EmbeddingSpace(
        model_space="test_space",
        model_id="test",
        model_revision="v1",
        dimensions=2,
        tokenizer_id="test",
        query_prefix="",
        passage_prefix="",
        normalization="none",
        metric="inner_product",
        max_input_tokens=512,
    )
    authority = Authority(app)
    for name, scores in memories:
        candidate = example("recall.MemoryCandidate")
        memory = {"scope": ctx.principal.home_scope.model_dump(), "memory_id": name, "version": 1}
        manifest = candidate["manifest"]
        manifest.update(
            memory=memory,
            model_space=space.model_space,
            dimensions=2,
            embedding_tokenizer="test",
            expected_chunk_count=len(scores),
        )
        chunks = []
        for index, _score in enumerate(scores):
            chunk = copy.deepcopy(manifest["chunks"][0])
            chunk.update(chunk_index=index, vector_id=fingerprint([name, index]))
            chunks.append(chunk)
        manifest["chunks"] = chunks
        guard = candidate["guard"]
        guard.update(
            memory=memory, checked_at=app.foundation.identity.clock(), authorization_epoch=1
        )
        for index, score in enumerate(scores):
            hit = copy.deepcopy(candidate["hits"][0])
            hit.update(
                memory=memory,
                model_space=space.model_space,
                chunk_index=index,
                vector_id=chunks[index]["vector_id"],
                input_hash=chunks[index]["input_hash"],
                score=score,
                rank=index + 1,
            )
            authority.proofs[hit["vector_id"]] = (copy.deepcopy(manifest), copy.deepcopy(guard))
            with app.foundation.uow.transaction() as tx:
                tx.write(
                    "generation_vectors", hit["vector_id"], {"hit": hit, "vector": [score, 0.0]}
                )
    encoder = Encoder()
    search = MemoryCandidates(
        app.foundation.uow,
        app.foundation.identity,
        encoder,
        SQLiteGenerationSearch(app.foundation.uow, app.foundation.identity),
        authority,
        EmbeddingSpaces((space,)),
    )
    request = MemorySearchRequest(
        operation_id="search",
        purpose="recall",
        query="query",
        selection=ScopeSelector(),
        model_space=space.model_space,
        memory_top_k=2,
        chunk_page_size=2,
        max_chunk_hits=20,
        max_rounds=10,
        deadline_at=ctx.deadline_at,
    )
    return ctx, search, authority, request


def test_unique_memory_top_k_and_real_sqlite_paging(app):
    ctx, search, authority, request = setup(app)
    result = asyncio.run(search.search(ctx, request))
    assert [c.memory.memory_id for c in result.candidates] == ["m1", "m2"]
    assert result.candidates[0].best_score == 0.95
    assert len(result.candidates[0].hits) == 2
    assert result.rounds_used == 2 and result.examined_chunk_hits == 4
    assert len(authority.calls) == 2
    with app.foundation.uow.transaction() as tx:
        assert not tx.rows("outbox")


@pytest.mark.parametrize("decision", ["excluded", "unverifiable"])
def test_unqualified_high_score_does_not_take_slot(app, decision):
    ctx, search, authority, request = setup(app)
    authority.decisions["m1"] = decision
    result = asyncio.run(search.search(ctx, request))
    assert [c.memory.memory_id for c in result.candidates] == ["m2", "m3"]
    assert result.coverage == ("complete" if decision == "excluded" else "partial")


@pytest.mark.parametrize("fault", ["missing", "duplicate"])
def test_qualification_response_must_cover_exact_targets(app, fault):
    ctx, search, authority, request = setup(app)
    authority.fault = fault
    with pytest.raises(FoundationError, match="response mismatch"):
        asyncio.run(search.search(ctx, request))


@pytest.mark.parametrize(
    "limit,reason", [("max_chunk_hits", "hit_limit"), ("max_rounds", "round_limit")]
)
def test_discovery_budget_not_claimed_complete(app, limit, reason):
    ctx, search, authority, request = setup(app)
    request = request.model_copy(update={limit: 2 if limit == "max_chunk_hits" else 1})
    result = asyncio.run(search.search(ctx, request))
    assert result.stop_reason == reason and result.coverage == "partial"
    assert len(result.candidates) == 1


def test_dependency_unavailable_is_not_empty_success(app):
    ctx, search, authority, request = setup(app)
    authority.fault = "unavailable"
    result = asyncio.run(search.search(ctx, request))
    assert result.coverage == "unavailable" and result.stop_reason == "dependency"


def test_scope_isolation_at_search_boundary(app):
    ctx, search, authority, request = setup(app)
    result = asyncio.run(search.search(context(app, "carol"), request))
    assert not result.candidates and not authority.calls


def test_corrupt_vector_rejected(app):
    ctx, search, authority, request = setup(app)
    with app.foundation.uow.transaction() as tx:
        key, row = tx.rows("generation_vectors")[0]
        row["vector"] = [1.0]
        tx.write("generation_vectors", key, row)
    with pytest.raises(FoundationError, match="corrupt"):
        asyncio.run(search.search(ctx, request))


def test_conflicting_revision_has_only_one_bounded_retry(app):
    ctx, search, authority, request = setup(app)
    first = next(iter(authority.proofs.values()))
    first[1]["relations_revision"] += 1
    result = asyncio.run(search.search(ctx, request))
    assert [c.memory.memory_id for c in result.candidates] == ["m2", "m3"]
    assert result.coverage == "partial"
    assert len(authority.calls) == 3


@pytest.mark.parametrize("fault", ["request", "repeat", "nan", "cross_tenant"])
def test_invalid_page_fails_closed(app, fault):
    ctx, search, authority, request = setup(app)
    original = search.vectors

    class BrokenPages:
        first = None

        async def search(self, ctx, req):
            page = await original.search(ctx, req)
            if fault == "repeat" and self.first:
                return self.first.model_copy(update={"request": req})
            self.first = page
            if fault == "request":
                return page.model_copy(
                    update={"request": req.model_copy(update={"operation_id": "wrong"})}
                )
            if fault == "nan":
                return page.model_copy(
                    update={"hits": (page.hits[0].model_copy(update={"score": float("nan")}),)}
                )
            if fault == "cross_tenant":
                ref = page.hits[0].memory.model_copy(
                    update={"scope": context(app, "carol").principal.home_scope}
                )
                return page.model_copy(
                    update={"hits": (page.hits[0].model_copy(update={"memory": ref}),)}
                )
            return page

    search.vectors = BrokenPages()
    with pytest.raises(FoundationError):
        asyncio.run(search.search(ctx, request))


def test_cancel_propagates(app):
    ctx, search, authority, request = setup(app)

    class Cancel:
        async def embed(self, ctx, req):
            raise asyncio.CancelledError()

    search.embedding = Cancel()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(search.search(ctx, request))


def test_revocation_during_qualification_not_degraded(app):
    ctx, search, authority, request = setup(app)
    original = authority.qualify

    async def revoke(ctx, targets, purpose):
        values = await original(ctx, targets, purpose)
        with app.foundation.uow.transaction() as tx:
            row = tx.read("identities", "alice")
            row["enabled"] = False
            tx.write("identities", "alice", row)
        return values

    authority.qualify = revoke
    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.search(ctx, request))
    assert failure.value.code == "FORBIDDEN"


@pytest.mark.parametrize("field,value", [("dimensions", 3), ("embedding_tokenizer", "wrong")])
def test_authority_manifest_matches_full_model_space(app, field, value):
    ctx, search, authority, request = setup(app)
    for manifest, _guard in authority.proofs.values():
        manifest[field] = value
    with pytest.raises(FoundationError, match="space mismatch"):
        asyncio.run(search.search(ctx, request))


def test_sync_dependency_cannot_overrun_shorter_request_deadline(app, monkeypatch):
    from aether_agent_memory.runtime.foundation.common import later

    ctx, search, authority, request = setup(app)
    start = app.foundation.identity.clock()
    request = request.model_copy(update={"deadline_at": later(start, 1)})
    clock = [start]
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: clock[0])
    original = authority.qualify

    async def slow(ctx, targets, purpose):
        result = await original(ctx, targets, purpose)
        clock[0] = later(start, 2)
        return result

    authority.qualify = slow
    result = asyncio.run(search.search(ctx, request))
    assert result.stop_reason == "deadline" and result.coverage == "unavailable"


def test_equal_scores_have_stable_memory_order_across_pages(app):
    ctx, search, authority, request = setup(app, (("z", (0.8,)), ("a", (0.8,))))
    request = request.model_copy(update={"chunk_page_size": 1})
    result = asyncio.run(search.search(ctx, request))
    assert [c.memory.memory_id for c in result.candidates] == ["a", "z"]


def test_retry_converges_without_excluded_high_score(app):
    ctx, search, authority, request = setup(app)
    first_id = next(iter(authority.proofs))
    authority.proofs[first_id][1]["relations_revision"] += 1
    original = authority.qualify

    async def converge(ctx, targets, purpose):
        result = await original(ctx, targets, purpose)
        if len(authority.calls) >= 2:
            return tuple(
                CandidateQualificationResult(
                    target=r.target,
                    decision="excluded",
                    reason_code="changed",
                    manifest=None,
                    guard=None,
                )
                if r.target.vector_id == first_id
                else r
                for r in result
            )
        return result

    authority.qualify = converge
    result = asyncio.run(search.search(ctx, request))
    assert result.candidates[0].memory.memory_id == "m1"
    assert result.candidates[0].best_score == 0.9
    assert len(result.candidates[0].hits) == 1
