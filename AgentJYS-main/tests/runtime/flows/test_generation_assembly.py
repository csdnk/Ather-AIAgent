"""Whole-body A planning against a B fixture with independently checked revisions."""
# 组包消费者测试：用独立可变的 B 正文、关系与发布证据验证 A 的完整性和预算边界。

import asyncio

import pytest
from test_flows import app as azure_app
from test_generation_candidates import example, setup

from aether_agent_memory.recall.basic.assembly import ContextAssembly
from aether_agent_memory.recall.contracts.foundation import RecallPlanRequest
from aether_agent_memory.remember.contracts.foundation import (
    FullBodyReadResult,
    MemoryRelationSnapshot,
    ProjectionReadiness,
)
from aether_agent_memory.remember.contracts.models import (
    ConflictGroup,
    EligibilityBatch,
    EligibilityResult,
    MemoryReadBatch,
    MemorySnapshot,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash

app = azure_app


class BodyAuthority:
    # 严格 B 测试替身：正文/关系可被用例独立改动，用来模拟读取或提交期间的数据变化。
    def __init__(self, app, proofs):
        # 准备具有明确结尾的完整正文，让测试能发现只取块文本或截断正文的错误。
        self.app, self.proofs = app, proofs
        self.snapshots, self.bodies = {}, {}
        self.conflicts = []
        self.working_ids = []
        self.body_fault = None
        self.guard_fault = None
        self.guarded = []
        self.deleted = set()
        self.pending_count = self.failed_count = 0
        for manifest, guard in proofs.values():
            key = guard["memory"]["memory_id"]
            content = "完整正文 " + key + " 保留结尾与全部事实。"
            digest = text_hash(content)
            manifest["body_hash"] = digest
            manifest["vector_location"]["content_hash"] = digest
            guard["body_hash"] = digest
            snapshot = example("remember.MemorySnapshot")
            snapshot.update(
                ref=guard["memory"],
                content=content,
                content_hash=digest,
                object_revision=guard["object_revision"],
                model_space=manifest["model_space"],
            )
            self.snapshots[key] = MemorySnapshot.model_validate(snapshot)
            body = example("remember.FullBodyReadResult")
            body.update(memory=guard["memory"], content=content, guard=guard)
            body["location"]["content_hash"] = digest
            self.bodies[key] = FullBodyReadResult.model_validate(body)
        with app.foundation.uow.transaction() as tx:
            for key, row in tx.rows("generation_vectors"):
                row["hit"]["body_hash"] = proofs[key][0]["body_hash"]
                tx.write("generation_vectors", key, row)

    def load(self, ctx, refs):
        with self.app.foundation.uow.transaction() as tx:
            self.app.foundation.identity.revalidate(tx, ctx)
        return MemoryReadBatch(
            items=tuple(
                self.snapshots[r.memory_id] for r in refs if r.memory_id not in self.deleted
            ),
            eligibility=EligibilityBatch(
                authorization_epoch=ctx.principal.auth_epoch,
                items=tuple(
                    EligibilityResult(
                        ref=r,
                        decision="excluded" if r.memory_id in self.deleted else "allowed",
                        reason="test",
                        checked_revision=1,
                    )
                    for r in refs
                ),
            ),
            conflicts=tuple(g for g in self.conflicts if any(r in g.members for r in refs)),
        )

    def working(self, ctx, selection, page):
        return self.load(ctx, tuple(self.snapshots[k].ref for k in self.working_ids))

    async def projection_readiness(self, ctx, selection, memory_source):
        return ProjectionReadiness(
            source=memory_source,
            ready_count=len(self.snapshots),
            pending_count=self.pending_count,
            failed_count=self.failed_count,
            complete=self.pending_count == self.failed_count == 0,
        )

    def relations(self, ctx, refs):
        return MemoryRelationSnapshot(
            guards=tuple(self.bodies[r.memory_id].guard for r in refs),
            conflicts=tuple(g for g in self.conflicts if any(r in g.members for r in refs)),
        )

    async def load_bodies(self, ctx, refs):
        result = tuple(self.bodies[r.memory_id] for r in refs)
        if self.body_fault == "missing_response":
            return result[:-1]
        if self.body_fault == "stale":
            first = result[0]
            changed = first.guard.model_copy(
                update={"relations_revision": first.guard.relations_revision + 1}
            )
            result = (first.model_copy(update={"guard": changed}), *result[1:])
        if self.body_fault == "unavailable_member":
            result = tuple(
                FullBodyReadResult(
                    memory=b.memory, outcome="unavailable", path="none", reason_code="unavailable"
                )
                if b.memory.memory_id == "m2"
                else b
                for b in result
            )
        return result

    def revalidate_context(self, tx, ctx, request):
        # 将预期凭据与替身的当前正文和发布清单逐项对照，验证失败通过真实 RF 事务中止。
        self.guarded.append(request)
        self.app.foundation.identity.revalidate(tx, ctx)
        for expected in request.expected:
            current = self.bodies[expected.memory.memory_id].guard
            if expected.memory.memory_id in self.deleted or current.model_dump(
                exclude={"checked_at"}
            ) != expected.model_dump(exclude={"checked_at"}):
                tx.abort(ErrorCode.RESULT_INVALIDATED, "B facts changed")
        for manifest in request.manifests:
            current = self.proofs[manifest.chunks[0].vector_id][0]
            if current != manifest.model_dump(mode="json"):
                tx.abort(ErrorCode.RESULT_INVALIDATED, "B generation changed")
        values = tuple(
            g.model_copy(update={"checked_at": self.app.foundation.identity.clock()})
            for g in request.expected
        )
        return values[:-1] if self.guard_fault == "missing" else values


def assembly_setup(app, sources=("long_term",), **updates):
    # 把真实 A 组包器接入 B 替身；为每个选定来源构造独立搜索请求。
    ctx, search, authority, search_request = setup(
        app, memory_sources={"m1": "working"} if "working" in sources else None
    )
    body = BodyAuthority(app, authority.proofs)
    app.recall.memories = body
    assembly = ContextAssembly(app.recall, search, body, body)
    request = RecallPlanRequest(
        recall_id="new_recall",
        query=search_request.query,
        selection=search_request.selection,
        sources=sources,
        token_budget=1024,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        long_term_search=search_request if "long_term" in sources else None,
        working_search=search_request.model_copy(update={"memory_source": "working"})
        if "working" in sources
        else None,
    )
    return ctx, assembly, body, request.model_copy(update=updates)


def test_whole_body_plan_persists_exact_generation_and_budget(app):
    ctx, assembly, body, request = assembly_setup(app)
    plan = asyncio.run(assembly.plan(ctx, request))
    assert len(plan.units) == 2
    assert all(b.content in plan.rendered_context for u in plan.units for b in u.bodies)
    assert plan.tokens_used == app.recall.tokenizer.count(plan.rendered_context)
    with app.foundation.uow.transaction() as tx:
        saved = tx.read("recall_assembly", request.recall_id)
        assert len(saved["expectations"]["manifests"]) == 2
        events = tx.rows("outbox")
        assert len(events) == 2 and all(r["event"]["payload"]["stage"] == "read" for _, r in events)


def test_working_only_uses_vector_candidates_and_published_manifest_without_rrf(app):
    ctx, assembly, body, request = assembly_setup(app, sources=("working",))
    body.working_ids = ["m1"]
    plan = asyncio.run(assembly.plan(ctx, request))
    assert assembly.candidates.embedding.calls == 1
    assert not plan.rank_evidence
    assert plan.units[0].primary_memories
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m1"]


def test_conflict_is_whole_and_related_memory_not_primary(app):
    ctx, assembly, body, request = assembly_setup(app)
    body.conflicts = [
        ConflictGroup(
            group_id="conflict",
            members=(body.snapshots["m1"].ref, body.snapshots["m3"].ref),
            explanation="both required",
        )
    ]
    plan = asyncio.run(assembly.plan(ctx, request))
    group = next(u for u in plan.units if u.conflict)
    assert len(group.bodies) == 2 and len(group.primary_memories) == 1


def test_missing_conflict_member_skips_whole_group_keeps_independent(app):
    ctx, assembly, body, request = assembly_setup(app)
    body.conflicts = [
        ConflictGroup(
            group_id="conflict",
            members=(body.snapshots["m1"].ref, body.snapshots["m3"].ref),
            explanation="both required",
        )
    ]
    body.deleted.add("m3")
    plan = asyncio.run(assembly.plan(ctx, request))
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
    assert "conflict" in plan.skipped_group_ids


def test_body_response_missing_is_contract_failure(app):
    ctx, assembly, body, request = assembly_setup(app)
    body.body_fault = "missing_response"
    with pytest.raises(FoundationError, match="body response"):
        asyncio.run(assembly.plan(ctx, request))


def test_changed_relation_stamp_cannot_reuse_candidate(app):
    ctx, assembly, body, request = assembly_setup(app)
    body.body_fault = "stale"
    plan = asyncio.run(assembly.plan(ctx, request))
    assert len(plan.units) == 1 and "body_stale" in plan.degradation_reasons


def test_budget_too_small_does_not_truncate(app):
    ctx, assembly, body, request = assembly_setup(app, token_budget=1)
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))
    assert error.value.code == "BUDGET_TOO_SMALL"


def test_mixed_sources_one_memory_one_rrf_contribution_per_source(app):
    ctx, assembly, body, request = assembly_setup(app, sources=("working", "long_term"))
    body.working_ids = ["m1"]
    plan = asyncio.run(assembly.plan(ctx, request))
    assert len(plan.units) == 3
    assert len([e for e in plan.rank_evidence if e.memory.memory_id == "m1"]) == 1
    assert {e.source for e in plan.rank_evidence} == {"working", "long_term"}


def test_reranker_requires_current_b_guards(app):
    from aether_agent_memory.recall.basic.config import RecallSettings

    ctx, assembly, body, request = assembly_setup(app)
    app.recall.settings = RecallSettings(reranker_model="test", rerank_policy="required")
    body.guard_fault = "missing"

    class ForbiddenReranker:
        async def rerank(self, ctx, query, documents):
            pytest.fail("plaintext sent before guards validated")

    app.recall.reranker = ForbiddenReranker()
    with pytest.raises(FoundationError, match="coverage mismatch"):
        asyncio.run(assembly.plan(ctx, request))


def test_long_term_failure_retains_verified_working(app):
    ctx, assembly, body, request = assembly_setup(app, sources=("working", "long_term"))
    body.working_ids = ["m1"]

    original = assembly.candidates

    class FailedCandidates:
        async def search(self, ctx, request):
            if request.memory_source == "long_term":
                raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "offline")
            return await original.search(ctx, request)

    assembly.candidates = FailedCandidates()
    plan = asyncio.run(assembly.plan(ctx, request))
    assert len(plan.units) == 1 and "long_term_dependency" in plan.degradation_reasons


def test_working_failure_retains_verified_long_term(app):
    ctx, assembly, body, request = assembly_setup(app, sources=("working", "long_term"))

    original = body.projection_readiness

    async def failed(ctx, selection, memory_source):
        if memory_source == "working":
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "offline")
        return await original(ctx, selection, memory_source)

    body.projection_readiness = failed
    plan = asyncio.run(assembly.plan(ctx, request))
    assert len(plan.units) == 2 and "working_dependency" in plan.degradation_reasons


def test_pending_working_without_hits_reports_pending_not_empty(app):
    ctx, assembly, body, request = assembly_setup(app, sources=("working",))
    body.pending_count = 1
    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("generation_vectors"):
            if row["hit"]["memory_source"] == "working":
                row["hit"]["memory_source"] = "long_term"
                tx.write("generation_vectors", key, row)
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))
    assert error.value.code == ErrorCode.REQUEST_IN_PROGRESS


def test_pending_working_keeps_verified_partial_results_with_coverage(app):
    ctx, assembly, body, request = assembly_setup(app, sources=("working",))
    body.pending_count = 1
    plan = asyncio.run(assembly.plan(ctx, request))
    assert len(plan.units) == 1 and "working_index_pending" in plan.degradation_reasons
    with app.foundation.uow.transaction() as tx:
        assert tx.read("recall_assembly", request.recall_id)["coverage"]["working"] == "partial"


def test_relation_membership_changed_between_reads_fails(app):
    ctx, assembly, body, request = assembly_setup(app)
    original = body.relations

    def changed(ctx, refs):
        value = original(ctx, refs)
        conflict = ConflictGroup(
            group_id="new",
            members=(body.snapshots["m1"].ref, body.snapshots["m2"].ref),
            explanation="new relation",
        )
        return value.model_copy(update={"conflicts": (conflict,)})

    body.relations = changed
    with pytest.raises(FoundationError, match="relation snapshot changed"):
        asyncio.run(assembly.plan(ctx, request))


def test_exact_rendered_budget_includes_number_and_separator(app):
    ctx, assembly, body, request = assembly_setup(app)
    first = body.bodies["m1"].content
    source_refs = ", ".join(f"{s.source_id}@{s.source_version}" for s in body.bodies["m1"].sources)
    expected = "[1] " + first + "\nSources: " + source_refs + "\n"
    budget = app.recall.tokenizer.count(expected)
    plan = asyncio.run(assembly.plan(ctx, request.model_copy(update={"token_budget": budget})))
    assert len(plan.units) == 1 and plan.tokens_used == budget
    assert plan.rendered_context == expected


@pytest.mark.parametrize("mode", ["success", "fallback", "invalid"])
def test_reranking_after_guard_and_explicit_failure_policy(app, mode):
    from aether_agent_memory.recall.basic.config import RecallSettings

    ctx, assembly, body, request = assembly_setup(app)
    app.recall.settings = RecallSettings(reranker_model="test", rerank_policy="fallback")

    class Rerank:
        async def rerank(self, ctx, query, documents):
            assert body.guarded and len(documents) == 2
            if mode == "fallback":
                raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "offline")
            return (0.0, float("nan") if mode == "invalid" else 1.0)

    app.recall.reranker = Rerank()
    if mode == "invalid":
        with pytest.raises(FoundationError, match="invalid rerank"):
            asyncio.run(assembly.plan(ctx, request))
    else:
        plan = asyncio.run(assembly.plan(ctx, request))
        if mode == "success":
            assert plan.units[0].bodies[0].memory.memory_id == "m2"
        else:
            assert "rerank_DEPENDENCY_UNAVAILABLE" in plan.degradation_reasons


@pytest.mark.parametrize("fault", ["missing", "revision"])
def test_allowed_snapshot_contract_checked(app, fault):
    ctx, assembly, body, request = assembly_setup(app)
    original = body.load

    def wrong(ctx, refs):
        batch = original(ctx, refs)
        if fault == "missing":
            return batch.model_copy(update={"items": batch.items[:-1]})
        verdicts = tuple(
            v.model_copy(update={"checked_revision": 99}) for v in batch.eligibility.items
        )
        return batch.model_copy(
            update={"eligibility": batch.eligibility.model_copy(update={"items": verdicts})}
        )

    body.load = wrong
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))
    assert error.value.code == "CONTRACT_VIOLATION"


def test_conflict_dependency_failure_preserves_independent_group(app):
    ctx, assembly, body, request = assembly_setup(app)
    body.conflicts = [
        ConflictGroup(
            group_id="conflict",
            members=(body.snapshots["m1"].ref, body.snapshots["m3"].ref),
            explanation="required",
        )
    ]
    original = body.load

    def unavailable(ctx, refs):
        if any(r.memory_id == "m3" for r in refs):
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "unavailable")
        return original(ctx, refs)

    body.load = unavailable
    plan = asyncio.run(assembly.plan(ctx, request))
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
    assert "relation_dependency" in plan.degradation_reasons


@pytest.mark.parametrize("where", ["metadata", "body"])
def test_explicit_exclusions_are_empty_not_dependency_failure(app, where):
    ctx, assembly, body, request = assembly_setup(app)
    if where == "metadata":
        body.deleted.update({"m1", "m2"})
    else:

        async def excluded(ctx, refs):
            return tuple(
                FullBodyReadResult(
                    memory=ref, outcome="excluded", path="none", reason_code="excluded"
                )
                for ref in refs
            )

        body.load_bodies = excluded
    plan = asyncio.run(assembly.plan(ctx, request))
    assert not plan.units and not plan.degradation_reasons
